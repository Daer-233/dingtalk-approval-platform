from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    """SQLite repository for tasks, mappings, profiles and approval attempts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    source_path TEXT NOT NULL, output_root TEXT NOT NULL,
                    error TEXT, data_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS company_profiles (
                    company TEXT PRIMARY KEY, payment_entity TEXT NOT NULL DEFAULT '',
                    supplier_name TEXT NOT NULL DEFAULT '', bank_account TEXT NOT NULL DEFAULT '',
                    bank_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS department_mappings (
                    company TEXT NOT NULL, source_department TEXT NOT NULL,
                    payment_department TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (company, source_department)
                );
                CREATE TABLE IF NOT EXISTS approval_role_scopes (
                    role_key TEXT PRIMARY KEY, role_name TEXT NOT NULL,
                    users_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_attempts (
                    task_id TEXT NOT NULL, group_id TEXT NOT NULL, status TEXT NOT NULL,
                    instance_id TEXT, error TEXT, request_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, group_id)
                );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item.update(json.loads(item.pop("data_json", "{}") or "{}"))
        return item

    def save_task(self, task: dict[str, Any]) -> None:
        columns = {"id", "status", "file_name", "created_at", "updated_at", "source_path", "output_root", "error"}
        data = {key: value for key, value in task.items() if key not in columns}
        with self.connect() as db:
            db.execute(
                """INSERT INTO tasks(id,status,file_name,created_at,updated_at,source_path,output_root,error,data_json)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,file_name=excluded.file_name,updated_at=excluded.updated_at,
                source_path=excluded.source_path,output_root=excluded.output_root,error=excluded.error,
                data_json=excluded.data_json""",
                (task["id"], task["status"], task["file_name"], task["created_at"], task["updated_at"],
                 task["source_path"], task["output_root"], task.get("error"), json.dumps(data, ensure_ascii=False)),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._decode(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [item for row in rows if (item := self._decode(row)) is not None]

    def list_profiles(self) -> list[dict[str, str]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM company_profiles ORDER BY company").fetchall()]

    def get_profile(self, company: str) -> dict[str, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM company_profiles WHERE company=?", (company,)).fetchone()
        return dict(row) if row else None

    def save_profile(self, profile: dict[str, str]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO company_profiles(company,payment_entity,supplier_name,bank_account,bank_name,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(company) DO UPDATE SET
                payment_entity=excluded.payment_entity,supplier_name=excluded.supplier_name,
                bank_account=excluded.bank_account,bank_name=excluded.bank_name,updated_at=excluded.updated_at""",
                tuple(profile[key] for key in ("company", "payment_entity", "supplier_name", "bank_account", "bank_name", "updated_at")),
            )

    def list_mappings(self) -> list[dict[str, str]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM department_mappings ORDER BY company,source_department").fetchall()]

    def mapping_dict(self) -> dict[tuple[str, str], str]:
        return {(item["company"], item["source_department"]): item["payment_department"] for item in self.list_mappings()}

    def save_mapping(self, company: str, source_department: str, payment_department: str, updated_at: str) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO department_mappings(company,source_department,payment_department,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(company,source_department) DO UPDATE SET
                payment_department=excluded.payment_department,updated_at=excluded.updated_at""",
                (company, source_department, payment_department, updated_at),
            )

    def list_approval_role_scopes(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM approval_role_scopes WHERE role_key NOT LIKE '@cc:%' ORDER BY role_key").fetchall()
        return [
            {
                "role_key": row["role_key"],
                "role_name": row["role_name"],
                "users": json.loads(row["users_json"] or "[]"),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_approval_role_scope(self, scope: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO approval_role_scopes(role_key,role_name,users_json,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(role_key) DO UPDATE SET
                role_name=excluded.role_name,users_json=excluded.users_json,updated_at=excluded.updated_at""",
                (scope["role_key"], scope["role_name"], json.dumps(scope.get("users", []), ensure_ascii=False), scope["updated_at"]),
            )

    def cc_defaults(self, corp_id: str, process_code: str) -> dict[str, Any]:
        import hashlib
        key = "@cc:" + hashlib.sha256(json.dumps([corp_id, process_code]).encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT * FROM approval_role_scopes WHERE role_key=?", (key,)).fetchone()
        return {"corp_id": corp_id, "process_code": process_code, "role_key": key,
                "users": json.loads(row["users_json"]) if row else [],
                "updated_at": row["updated_at"] if row else None}

    def save_cc_defaults(self, corp_id: str, process_code: str, users: list[dict[str, str]], updated_at: str) -> dict[str, Any]:
        item = self.cc_defaults(corp_id, process_code)
        self.save_approval_role_scope({**item, "role_name": "固定抄送人", "users": users, "updated_at": updated_at})
        return self.cc_defaults(corp_id, process_code)

    def approval_attempts(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM approval_attempts WHERE task_id=? ORDER BY updated_at", (task_id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json", "{}") or "{}")
            items.append(item)
        return items

    def get_attempt(self, task_id: str, group_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM approval_attempts WHERE task_id=? AND group_id=?", (task_id, group_id)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json", "{}") or "{}")
        return item

    def save_attempt(self, item: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO approval_attempts(task_id,group_id,status,instance_id,error,request_id,payload_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id,group_id) DO UPDATE SET
                status=excluded.status,instance_id=excluded.instance_id,error=excluded.error,
                request_id=excluded.request_id,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (item["task_id"], item["group_id"], item["status"], item.get("instance_id"), item.get("error"),
                 item["request_id"], json.dumps(item.get("payload", {}), ensure_ascii=False), item["updated_at"]),
            )
