from __future__ import annotations



import asyncio

import hashlib
import gc

import io

import shutil

import time

import uuid

import zipfile

from functools import wraps
from decimal import Decimal
from openpyxl import load_workbook
from datetime import datetime, timezone

from pathlib import Path

from typing import Any

from urllib.parse import quote



from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field



from .db import Database

from .dingtalk import (

    DingTalkClient,

    DingTalkError,

    DingTalkUncertainSubmissionError,

    apply_local_role_scopes,

    build_display_fields,

    forecast_signature,

    form_values_from_schema,

    form_submission_diagnostics,

    normalize_forecast,
    resolve_display_fields_from_schema,

    validate_node_actioners,

)

from .legacy_chain import fingerprint
from .modern_chain import build_modern_template_chain as build_legacy_chain
from .platform_dingtalk import normalize_forecast, validate_node_actioners
from .experiment import DiagnosticDingTalkClient as DingTalkClient, BLOCK_MESSAGE
from .experiment_diagnostics import build_report, payload_for_preview, summarize, historical_replay, redact, failure_record
from .approval_cc import seed_cc_nodes, compatibility
from .platform_paths import CHAIN_VERSION as FORECAST_FORMAT_VERSION
from .office_bridge import AuthorizedOfficeImportError, export_authorized_workbook

from .splitter import analyze_payment_allowances, build_rules, generate_selected_payment_files

from .spreadsheet_import import SpreadsheetImportError, normalize_workbook, workbook_extension

from .submission import run_limited, submission_progress



ROOT_DIR = Path(__file__).resolve().parents[1]

APP_DATA_DIR = ROOT_DIR / "data"  # Experiment data is always confined to this copy.

UPLOAD_DIR, OUTPUT_DIR = APP_DATA_DIR / "uploads", APP_DATA_DIR / "outputs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

db = Database(APP_DATA_DIR / "workflow.db")

dingtalk = DingTalkClient()

submission_locks: dict[str, asyncio.Lock] = {}

submission_jobs: set[asyncio.Task[Any]] = set()



DEFAULT_APPROVAL_ROLE_SCOPES = [

    {"role_key": "director", "role_name": "项目/设计总监"},

    {"role_key": "manager", "role_name": "项目经理"},

    {"role_key": "department_head", "role_name": "费用发生部门主管"},

]



app = FastAPI(title="审批诊断实验版 API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])





def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def unlink_quietly(path: Path) -> None:
    for delay in (0, 0.05, 0.2):
        if delay:
            time.sleep(delay)
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            gc.collect()
        except OSError:
            return

def public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {key: value for key, value in task.items() if key not in {"source_path", "output_root"}}
    if isinstance(item.get("result"), dict):
        result = dict(item["result"])
        result["departments"] = [{key: value for key, value in department.items() if key != "file_path"} for department in result.get("departments", [])]
        for key in ("summary", "zip", "detail_folder", "output_directory"):
            result.pop(key, None)
        item["result"] = result
    current_group_ids = {
        str(group.get("id"))
        for group in (task.get("result") or {}).get("departments", [])
        if group.get("id")
    }
    attempts = [
        attempt for attempt in db.approval_attempts(task["id"])
        if not current_group_ids or attempt["group_id"] in current_group_ids
    ]
    item["approval_attempts"] = attempts
    item["submission_progress"] = submission_progress(len(current_group_ids), attempts)
    return item

def save_attempt_stage(attempt: dict[str, Any], stage: str, status: str = "submitting", **updates: Any) -> None:
    payload = dict(attempt.get("payload") or {})
    payload["stage"] = stage
    attempt.update({"status": status, "payload": payload, "updated_at": utc_now(), **updates})
    db.save_attempt(attempt)

def recover_interrupted_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    if attempt.get("status") not in {"queued", "submitting"}:
        return attempt
    stage = str((attempt.get("payload") or {}).get("stage") or "queued")
    if stage == "creating":
        save_attempt_stage(attempt, stage, "uncertain", error='上次服务在创建钉钉审批时中断，结果待确认，请先核对钉钉审批列表。')
    else:
        save_attempt_stage(attempt, stage, "failed", error='上次提交在创建审批前中断，可重试该部门。')
    return attempt

def get_task_or_404(task_id: str) -> dict[str, Any]:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task

def exclusive_task_edit(handler):
    """Serialize task edits with submission, including async network/file work."""
    @wraps(handler)
    async def guarded(task_id: str, *args, **kwargs):
        lock = submission_locks.setdefault(task_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail="该任务正在处理，请等待当前操作完成后再修改或提交。")
        await lock.acquire()
        try:
            return await handler(task_id, *args, **kwargs)
        finally:
            lock.release()
    return guarded

class GroupChoice(BaseModel):
    group_id: str
    selected: bool = True
    payment_department: str = ""
    remember_mapping: bool = True
    # Retained only to load task JSON written by the old fixed-role UI.
    approvers: dict[str, dict[str, str]] = Field(default_factory=dict)

class TaskChoices(BaseModel):
    groups: list[GroupChoice]

class CompanyProfile(BaseModel):
    company: str
    payment_entity: str
    supplier_name: str
    bank_account: str
    bank_name: str

class ConfirmSubmit(BaseModel):
    confirm: bool = False
    confirm_legacy_chain: bool = False
    preview_fingerprints: dict[str, str] = Field(default_factory=dict)

class SelectedUser(BaseModel):
    user_id: str
    name: str = ""

class ApprovalCCDefaults(BaseModel):
    corp_id: str
    process_code: str
    users: list[SelectedUser] = Field(default_factory=list, max_length=20)

class ApprovalRoleScope(BaseModel):
    role_key: str = Field(min_length=1, max_length=80)
    role_name: str = Field(min_length=1, max_length=80)
    users: list[SelectedUser] = Field(default_factory=list, max_length=500)

class ApprovalRoleScopes(BaseModel):
    scopes: list[ApprovalRoleScope] = Field(default_factory=list, max_length=50)

class CustomNodeScope(BaseModel):
    role_name: str = Field(min_length=1, max_length=80)
    users: list[SelectedUser] = Field(min_length=1, max_length=30)

class WorkflowActioners(BaseModel):
    groups: dict[str, dict[str, list[SelectedUser]]] = Field(default_factory=dict)

class SelectedDownloads(BaseModel):
    indices: list[int] = Field(default_factory=list)

async def save_and_analyze(
    file: UploadFile,
    upload_path: Path,
    input_path: Path,
    original_name: str,
    import_mode: str = "标准上传",
) -> dict[str, Any]:
    error_detail: str | None = None
    try:
        with upload_path.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        await asyncio.to_thread(normalize_workbook, upload_path, input_path)
        analysis = await asyncio.to_thread(analyze_payment_allowances, input_path, db.mapping_dict())
    except Exception as exc:
        error_detail = str(exc)
    if error_detail is not None:
        unlink_quietly(upload_path)
        unlink_quietly(input_path)
        raise HTTPException(status_code=400, detail=error_detail)
    if upload_path != input_path:
        unlink_quietly(upload_path)
    task_id = input_path.stem.split("-")[0]
    task = {
        "id": task_id, "status": "review", "file_name": original_name, "created_at": utc_now(), "updated_at": utc_now(),
        "source_path": str(input_path), "output_root": str(OUTPUT_DIR / task_id), "error": None,
        "analysis": analysis, "choices": [], "result": None, "previews": [], "workflow_forecasts": {}, "workflow_actioners": {}, "import_mode": import_mode,
    }
    db.save_task(task)
    return public_task(task)

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "allowance-workbench", "edition": "审批诊断实验版", "read_only": True, "database": "sqlite", "dingtalk_configured": not dingtalk.config.missing}

@app.get("/api/rules")
async def rules() -> dict[str, Any]:
    items = [{"payment_department": rule.payment_department, "company": rule.company, "scope": rule.scope, "filename_stem": rule.filename_stem} for rule in build_rules("当月")]
    return {"count": len(items), "mode": "dynamic", "description": "按实际数据动态分组，以下规则仅用于特殊合并。", "items": items}

@app.post("/api/tasks")
async def create_task(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = file.filename or "上传文件.xlsx"
    try:
        suffix = workbook_extension(original_name)
    except SpreadsheetImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{task_id}{suffix}"
    input_path = UPLOAD_DIR / f"{task_id}.xlsx"
    import_mode = "标准上传" if suffix == ".xlsx" else f"{suffix[1:].upper()} 转换导入"
    return await save_and_analyze(file, upload_path, input_path, original_name, import_mode)

@app.post("/api/tasks/authorized-office")
async def create_authorized_office_task(file: UploadFile = File(...), password: str = Form("")) -> dict[str, Any]:
    original_name = file.filename or "上传文件.xlsx"
    if not original_name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="本机 WPS / Microsoft Excel 授权导入仅支持 .xlsx；.ods 和 .numbers 请使用标准上传。")
    task_id = uuid.uuid4().hex
    source_path, input_path = UPLOAD_DIR / f"{task_id}-office-source.xlsx", UPLOAD_DIR / f"{task_id}.xlsx"
    try:
        with source_path.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        await asyncio.to_thread(export_authorized_workbook, source_path, input_path, password)
    except AuthorizedOfficeImportError as exc:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        source_path.unlink(missing_ok=True)
    # save_and_analyze expects an UploadFile, so analyze the authorised copy directly.
    try:
        analysis = await asyncio.to_thread(analyze_payment_allowances, input_path, db.mapping_dict())
    except Exception as exc:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task = {"id": task_id, "status": "review", "file_name": original_name, "created_at": utc_now(), "updated_at": utc_now(),
            "source_path": str(input_path), "output_root": str(OUTPUT_DIR / task_id), "error": None, "analysis": analysis,
            "choices": [], "result": None, "previews": [], "workflow_forecasts": {}, "workflow_actioners": {}, "import_mode": "本机 WPS / Microsoft Excel 授权导入"}
    db.save_task(task)
    return public_task(task)

@app.get("/api/tasks")
async def list_tasks() -> dict[str, Any]:
    return {"items": [public_task(task) for task in db.list_tasks(30)]}

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    return public_task(get_task_or_404(task_id))

@app.put("/api/tasks/{task_id}/groups")
@exclusive_task_edit
async def update_groups(task_id: str, body: TaskChoices) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    analysis_groups = {group["id"]: group for group in task["analysis"]["groups"]}
    choices = []
    for choice_model in body.groups:
        choice = choice_model.model_dump()
        source = analysis_groups.get(choice["group_id"])
        if not source:
            raise HTTPException(status_code=400, detail=f"分组不存在：{choice['group_id']}")
        if choice["selected"] and not choice["payment_department"].strip():
            raise HTTPException(status_code=400, detail=f"{source['company']} / {source['suggested_department']} 尚未指定付款部门。")
        choices.append(choice)
        if choice["remember_mapping"] and not source["known_rule"] and choice["payment_department"].strip():
            for source_department in source["source_departments"]:
                db.save_mapping(source["company"], source_department, choice["payment_department"].strip(), utc_now())
    task.update({"choices": choices, "status": "review", "result": None, "previews": [], "workflow_forecasts": {}, "workflow_actioners": {}, "updated_at": utc_now(), "error": None})
    db.save_task(task)
    return public_task(task)

@app.post("/api/tasks/{task_id}/prepare")
@exclusive_task_edit
async def prepare_task(task_id: str) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    if not task.get("choices"):
        raise HTTPException(status_code=409, detail="请先确认本次付款部门和未知部门归属。")
    try:
        result = await asyncio.to_thread(generate_selected_payment_files, Path(task["source_path"]), Path(task["output_root"]), task["choices"], db.mapping_dict())
        task["cc_defaults_snapshot"] = current_cc_defaults()
        task.update({"result": result, "status": "prepared", "previews": [], "workflow_forecasts": {}, "workflow_actioners": {}, "updated_at": utc_now(), "error": None})
        db.save_task(task)
    except Exception as exc:
        task.update({"status": "failed", "error": str(exc), "updated_at": utc_now()})
        db.save_task(task)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_task(task)

@app.get("/api/company-profiles")
async def company_profiles() -> dict[str, Any]:
    return {"items": db.list_profiles()}

@app.put("/api/company-profiles/{company}")
async def update_company_profile(company: str, profile: CompanyProfile) -> dict[str, Any]:
    if company != profile.company:
        raise HTTPException(status_code=400, detail="公司主体与路径不一致。")
    item = profile.model_dump()
    item["updated_at"] = utc_now()
    db.save_profile(item)
    return item

def approval_role_scope_items() -> list[dict[str, Any]]:
    saved = {item["role_key"]: item for item in db.list_approval_role_scopes()}
    items = []
    for default in DEFAULT_APPROVAL_ROLE_SCOPES:
        item = saved.pop(default["role_key"], None) or {**default, "users": [], "updated_at": None}
        items.append(item)
    items.extend(saved.values())
    return items

def current_cc_defaults() -> dict[str, Any]:
    item = db.cc_defaults(dingtalk.config.corp_id, dingtalk.config.process_code)
    return {key: value for key, value in item.items() if key != "role_key"}

@app.get("/api/approval-cc-defaults")
async def get_cc_defaults() -> dict[str, Any]:
    return current_cc_defaults()

@app.put("/api/approval-cc-defaults")
async def put_cc_defaults(body: ApprovalCCDefaults) -> dict[str, Any]:
    if (body.corp_id, body.process_code) != (dingtalk.config.corp_id, dingtalk.config.process_code):
        raise HTTPException(status_code=409, detail="企业或审批模板已变化，请重新读取固定抄送配置。")
    if not body.corp_id or not body.process_code:
        raise HTTPException(status_code=409, detail="请先配置企业和 processCode。")
    ids = [user.user_id.strip() for user in body.users]
    if any(not key or "," in key for key in ids) or len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="固定抄送 UserId 无效或重复。")
    try:
        verified = await asyncio.to_thread(dingtalk.users_by_ids, ids) if ids else []
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    people = {user["user_id"]: user for user in verified}
    if any(key not in people for key in ids):
        raise HTTPException(status_code=400, detail="部分抄送人不在应用获授权的通讯录中，配置未保存。")
    db.save_cc_defaults(body.corp_id, body.process_code,
                        [{"user_id": key, "name": people[key].get("name") or key} for key in ids], utc_now())
    return current_cc_defaults()

@app.get("/api/approval-role-scopes")
async def approval_role_scopes() -> dict[str, Any]:
    return {"items": approval_role_scope_items()}

@app.put("/api/approval-role-scopes")
async def update_approval_role_scopes(body: ApprovalRoleScopes) -> dict[str, Any]:
    seen_keys: set[str] = set()
    for model in body.scopes:
        scope = model.model_dump()
        role_key = scope["role_key"].strip()
        if role_key.startswith("@"):
            raise HTTPException(status_code=400, detail="不能通过人员范围接口修改保留配置。")
        if role_key in seen_keys:
            raise HTTPException(status_code=400, detail=f"审批角色重复：{role_key}")
        seen_keys.add(role_key)
        users: list[dict[str, str]] = []
        seen_users: set[str] = set()
        for user in scope["users"]:
            user_id = str(user.get("user_id") or "").strip()
            if user_id and user_id not in seen_users:
                users.append({"user_id": user_id, "name": str(user.get("name") or user_id).strip()})
                seen_users.add(user_id)
        db.save_approval_role_scope({
            "role_key": role_key,
            "role_name": scope["role_name"].strip(),
            "users": users,
            "updated_at": utc_now(),
        })
    return {"items": approval_role_scope_items()}

@app.post("/api/approval-role-scopes/candidates")
async def append_node_scope_candidates(body: CustomNodeScope) -> dict[str, Any]:
    """Append directory-verified candidates for default or custom manual nodes.

    This is a local candidate list, not a way to widen a template's explicit range.
    Forecast normalization intersects both lists before the user can submit.
    """
    name = body.role_name.strip()
    normalized = "".join(name.split()).replace("／", "/")
    if not normalized:
        raise HTTPException(status_code=400, detail="审批节点名称不能为空。")
    requested = list(dict.fromkeys(user.user_id.strip() for user in body.users if user.user_id.strip()))
    if not requested:
        raise HTTPException(status_code=400, detail="请先添加钉钉通讯录人员。")
    try:
        verified = await asyncio.to_thread(dingtalk.users_by_ids, requested)
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    verified_by_id = {user["user_id"]: user for user in verified}
    if any(user_id not in verified_by_id for user_id in requested):
        raise HTTPException(status_code=400, detail="部分人员不在应用可访问的钉钉通讯录中。")
    existing = next((item for item in approval_role_scope_items()
                     if "".join(item["role_name"].split()).replace("／", "/") == normalized), None)
    if not existing and len(approval_role_scope_items()) >= 50:
        raise HTTPException(status_code=400, detail="自定义审批节点范围已达到数量上限。")
    role_key = existing["role_key"] if existing else "custom_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    combined = {user["user_id"]: user for user in (existing or {}).get("users", [])}
    for user_id in requested:
        user = verified_by_id[user_id]
        combined[user_id] = {"user_id": user_id, "name": user.get("name") or user_id}
    if len(combined) > 500:
        raise HTTPException(status_code=400, detail="该节点可选人员不能超过 500 人。")
    scope = {"role_key": role_key, "role_name": name, "users": list(combined.values()), "updated_at": utc_now()}
    db.save_approval_role_scope(scope)
    return scope

@app.post("/api/approval-role-scopes/custom")
async def add_custom_node_scope(body: CustomNodeScope) -> dict[str, Any]:
    # Preserve the legacy custom-only API contract; the picker uses the append API.
    normalized = "".join(body.role_name.split()).replace("／", "/")
    if normalized in {"".join(item["role_name"].split()).replace("／", "/") for item in DEFAULT_APPROVAL_ROLE_SCOPES}:
        raise HTTPException(status_code=400, detail="预设节点请在付款资料中维护人员范围。")
    return await append_node_scope_candidates(body)

@app.get("/api/department-mappings")
async def department_mappings() -> dict[str, Any]:
    return {"items": db.list_mappings()}

@app.get("/api/dingtalk/preflight")
async def dingtalk_preflight(remote: bool = Query(False)) -> dict[str, Any]:
    if not remote:
        dingtalk.reload_config()
    return await asyncio.to_thread(dingtalk.preflight, remote)

@app.get("/api/dingtalk/users")
async def dingtalk_users(keyword: str = Query("", max_length=60), user_ids: str = Query("", max_length=4000)) -> dict[str, Any]:
    if dingtalk.config.missing:
        return {"items": [], "configured": False}
    try:
        explicit_ids = [value.strip() for value in user_ids.split(",") if value.strip()]
        items = await asyncio.to_thread(dingtalk.users_by_ids, explicit_ids) if explicit_ids else await asyncio.to_thread(dingtalk.list_users, keyword)
        return {"items": items, "configured": True}
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

def _profile_errors(profile: dict[str, Any]) -> list[str]:
    missing = [label for key, label in (("payment_entity", "付款主体"), ("supplier_name", "供应商名称"), ("bank_account", "银行账号"), ("bank_name", "开户银行")) if not profile.get(key)]
    return ["公司付款资料缺少：" + "、".join(missing)] if missing else []

def _node_actioners(task: dict[str, Any], group_id: str) -> dict[str, list[dict[str, str]]]:
    raw = (task.get("workflow_actioners") or {}).get(group_id) or {}
    return {
        str(actor_key): [
            {"user_id": str(user.get("user_id") or ""), "name": str(user.get("name") or ""),
             **({"source": user["source"]} if user.get("source") in {"template", "local_default"} else {})}
            for user in users if isinstance(user, dict)
        ]
        for actor_key, users in raw.items() if isinstance(users, list)
    }

def normalized_scoped_forecast(raw: dict[str, Any], task: dict[str, Any] | None = None, group_id: str = "") -> dict[str, Any]:
    forecast = normalize_forecast(raw)
    apply_local_role_scopes(forecast["nodes"], approval_role_scope_items())
    if task is not None:
        forecast["node_actioners"] = seed_cc_nodes(forecast["nodes"], task.get("cc_defaults_snapshot") or {}, _node_actioners(task, group_id))
        forecast["compatibility"] = compatibility(forecast["nodes"], forecast["node_actioners"])
    return forecast

def preview_items(task: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    result = task.get("result") or {}
    items, all_valid = [], True
    for group in result.get("departments", []):
        profile = db.get_profile(group["company"]) or {"company": group["company"], "payment_entity": "", "supplier_name": "", "bank_account": "", "bank_name": ""}
        forecast = (task.get("workflow_forecasts") or {}).get(group["id"]) or {}
        nodes = forecast.get("nodes") or []
        actioners = _node_actioners(task, group["id"])
        errors = _profile_errors(profile)
        checked = compatibility(nodes, actioners)
        chain = checked["chain"]
        if forecast.get("error"):
            errors.append("流程预测失败：" + str(forecast["error"]))
        elif not forecast:
            errors.append("尚未检测钉钉审批流程。")
        else:
            errors.extend(checked["errors"])
            if any(node.get("metadata_version") != FORECAST_FORMAT_VERSION for node in nodes):
                errors.append("平台已更新，当前任务仍保存旧版流程预测。请点击“重新检测审批路径”，核对人员后重新生成审批预览。")
            if forecast.get("signature") != forecast_signature(nodes):
                errors.append("审批预测版本已更新或节点信息变化，请重新检测流程。")
        fields = build_display_fields(group, profile, result.get("month", "当月"), group["file_name"], dingtalk.config.effective_applicant_dept_id)
        if forecast.get("resolved_fields") and forecast.get("source_fields_fingerprint") != fingerprint(fields):
            errors.append("付款资料或表单数据已变化，请重新检测流程并生成预览。")
        fields = forecast.get("resolved_fields") or fields
        snapshot = task.get("cc_defaults_snapshot") or {}
        if snapshot and (snapshot.get("corp_id"), snapshot.get("process_code")) != (dingtalk.config.corp_id, dingtalk.config.process_code):
            errors.append("本次附件对应的企业或模板已变化，请重新生成附件。")
        errors = list(dict.fromkeys(errors))
        valid = not errors
        all_valid = all_valid and valid
        items.append({"group_id": group["id"], "company": group["company"], "payment_department": group["payment_department"],
                      "count": group["count"], "amount": group["amount"], "attachment_name": group["file_name"], "nodes": nodes,
                      "node_actioners": actioners, "signature": forecast.get("signature", ""), "fields": fields,
                      "legacy_chain": chain, "compatibility": checked, "valid": valid, "errors": errors})
        items[-1]["confirmation_fingerprint"] = fingerprint({
            "group_id": group["id"], "company": group["company"], "amount": group["amount"],
            "attachment_name": group["file_name"], "file_path": group.get("file_path", ""),
            "fields": fields, "profile": profile, "signature": forecast.get("signature", ""),
            "node_actioners": actioners, "chain": chain["fingerprint"],
            "submit_config": [dingtalk.config.agent_id, dingtalk.config.process_code,
                              dingtalk.config.originator_user_id, dingtalk.config.originator_dept_id],
        })
        try:
            schema = forecast.get("schema_snapshot")
            if schema:
                payload = payload_for_preview(dingtalk, schema, fields, nodes, actioners)
                report = build_report(schema, payload, forecast, actioners, expected_amount=group["amount"],
                                      local_errors=errors, pending_attachment=True)
            else:
                from .experiment_diagnostics import inspect_people
                people, findings = inspect_people(nodes, actioners)
                findings += [{"code": "SCHEMA_UNVERIFIED", "level": "unverified", "field": "表单", "message": "预览未保存模板快照，请运行只读诊断。"}]
                findings += [{"code": "LOCAL_PREVIEW", "level": "error", "field": "流程", "message": e} for e in errors]
                report = {"mode": "read_only", "personnel": people, "findings": findings, "failure": None,
                          "real_create_calls": 0, "real_upload_calls": 0}
                report["diagnostic_summary"] = summarize(report)
        except Exception as exc:
            report = {"mode": "read_only", "personnel": [], "failure": None, "findings": [
                {"code": "PREVIEW_DIAGNOSTIC_UNVERIFIED", "level": "unverified", "field": "诊断", "message": f"预览诊断未完成：{type(exc).__name__}，请运行只读诊断。"}]}
            report["diagnostic_summary"] = summarize(report)
        items[-1]["diagnostics"] = report
        items[-1]["diagnostic_summary"] = report["diagnostic_summary"]
    return items, all_valid

@app.post("/api/tasks/{task_id}/approvals/forecast")
@exclusive_task_edit
async def approval_forecast(task_id: str, body: WorkflowActioners | None = None) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    if not task.get("result"):
        raise HTTPException(status_code=409, detail="请先生成选中部门的附件。")
    try:
        schema = await asyncio.to_thread(dingtalk.get_schema)
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if body is not None and body.groups:
        task["workflow_actioners"] = {group_id: {key: [user.model_dump() for user in users] for key, users in values.items()} for group_id, values in body.groups.items()}
    forecasts: dict[str, dict[str, Any]] = {}
    seeded_actioners = dict(task.get("workflow_actioners") or {})
    for group in task["result"].get("departments", []):
        profile = db.get_profile(group["company"]) or {}
        fields = build_display_fields(group, profile, task["result"].get("month", "当月"), group["file_name"], dingtalk.config.effective_applicant_dept_id)
        source_fields_fingerprint = fingerprint(fields)
        try:
            fields = resolve_display_fields_from_schema(schema, fields)
            raw = await asyncio.to_thread(dingtalk.forecast, form_values_from_schema(schema, fields))
            normalized = normalized_scoped_forecast(raw, task, group["id"])
            seeded_actioners[group["id"]] = normalized.get("node_actioners", {})
            if not normalized["success"]:
                raise DingTalkError("钉钉未返回可用的流程预测结果")
            forecasts[group["id"]] = {
                **normalized,
                "name": f"{group['company']} · {group['payment_department']}",
                "signature": forecast_signature(normalized["nodes"]),
                "predicted_at": utc_now(),
                "resolved_fields": fields,
                "source_fields_fingerprint": source_fields_fingerprint,
                "schema_snapshot": schema,
                "error": None,
            }
        except Exception as exc:
            forecasts[group["id"]] = {
                "nodes": [],
                "name": f"{group['company']} · {group['payment_department']}",
                "signature": "",
                "predicted_at": utc_now(),
                "error": str(exc),
            }
    task.update({"workflow_actioners": seeded_actioners, "workflow_forecasts": forecasts, "previews": [], "status": "prepared", "updated_at": utc_now()})
    db.save_task(task)
    return {"groups": [{"group_id": group_id, **forecast, "node_actioners": _node_actioners(task, group_id)} for group_id, forecast in forecasts.items()]}

@app.put("/api/tasks/{task_id}/approvals/actioners")
@exclusive_task_edit
async def update_workflow_actioners(task_id: str, body: WorkflowActioners) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    forecasts = task.get("workflow_forecasts") or {}
    cleaned: dict[str, dict[str, list[dict[str, str]]]] = {}
    for group_id, selections in body.groups.items():
        if group_id not in forecasts:
            raise HTTPException(status_code=400, detail=f"付款分组尚未完成流程检测：{group_id}")
        nodes = forecasts[group_id].get("nodes") or []
        values = {key: [user.model_dump() for user in users] for key, users in selections.items()}
        errors = validate_node_actioners(nodes, values)
        if errors:
            raise HTTPException(status_code=400, detail="；".join(errors))
        cleaned[group_id] = values
    task.update({"workflow_actioners": cleaned, "previews": [], "status": "prepared", "updated_at": utc_now()})
    db.save_task(task)
    return {"groups": cleaned}

@app.post("/api/tasks/{task_id}/approvals/preview")
@exclusive_task_edit
async def approval_preview(task_id: str) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    if not task.get("result"):
        raise HTTPException(status_code=409, detail="请先生成选中部门的附件。")
    items, valid = preview_items(task)
    current_group_ids = {item["group_id"] for item in items}
    attempts = {
        attempt["group_id"]: attempt
        for attempt in db.approval_attempts(task_id)
        if attempt["group_id"] in current_group_ids
    }
    statuses = {attempt["status"] for attempt in attempts.values()}
    if statuses & {"queued", "submitting"}:
        status = "submitting"
    elif current_group_ids and all(
        attempts.get(group_id, {}).get("status") == "submitted"
        for group_id in current_group_ids
    ):
        status = "submitted"
    elif "submitted" in statuses:
        status = "partial_failed"
    elif statuses & {"failed", "uncertain"}:
        status = "failed"
    else:
        status = "previewed"
    task.update({"previews": items, "status": status, "updated_at": utc_now()})
    db.save_task(task)
    return {"items": items, "valid": valid, "connection": dingtalk.public_config()}

def finalize_submission(task: dict[str, Any], total: int) -> dict[str, Any]:
    group_ids = {group["id"] for group in (task.get("result") or {}).get("departments", [])}
    attempts = [
        attempt for attempt in db.approval_attempts(task["id"])
        if attempt["group_id"] in group_ids
    ]
    submitted = sum(1 for attempt in attempts if attempt["status"] == "submitted")
    failed = sum(1 for attempt in attempts if attempt["status"] == "failed")
    uncertain = sum(1 for attempt in attempts if attempt["status"] == "uncertain")
    if total and submitted == total:
        final_status = "submitted"
    elif submitted:
        final_status = "partial_failed"
    elif failed or uncertain:
        final_status = "failed"
    else:
        final_status = "previewed"
    problems = []
    if failed:
        problems.append(f"{failed} 条审批发起失败")
    if uncertain:
        problems.append(f"{uncertain} 条审批结果待确认")
    task.update({"status": final_status, "updated_at": utc_now(), "error": "；".join(problems) or None})
    db.save_task(task)
    return {
        "status": final_status,
        "submitted": submitted,
        "failed": failed,
        "uncertain": uncertain,
        "progress": submission_progress(total, attempts),
    }

def queue_submission(
    task: dict[str, Any], failed_only: bool,
    confirmation: ConfirmSubmit | None = None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]], int]:
    if not dingtalk.config.live_submit_enabled:
        raise HTTPException(status_code=409, detail="正式提交开关未开启。请先使用测试审批模板完成联调。")
    confirmation = confirmation or ConfirmSubmit()
    if not confirmation.confirm:
        raise HTTPException(status_code=400, detail="正式提交前必须完成批量确认。")
    items, _ = preview_items(task)
    saved = {item["group_id"]: item for item in task.get("previews") or []}
    eligible = []
    for item in items:
        existing = db.get_attempt(task["id"], item["group_id"])
        if existing:
            existing = recover_interrupted_attempt(existing)
        if existing and existing["status"] in {"submitted", "uncertain"}:
            continue
        if failed_only and (not existing or existing["status"] != "failed"):
            continue
        eligible.append((item, existing))
    # Validate every candidate before writing a single queue record.
    for item, _ in eligible:
        if not item["valid"]:
            raise HTTPException(status_code=409, detail="审批预览仍有未完成配置：" + "；".join(item["errors"]))
        key = item["group_id"]
        expected = item["confirmation_fingerprint"]
        if not saved.get(key, {}).get("valid") or saved[key].get("confirmation_fingerprint") != expected or confirmation.preview_fingerprints.get(key) != expected:
            raise HTTPException(status_code=409, detail="预览未生成或已变化，请重新生成审批预览并确认，未创建任何审批。")
        if item["legacy_chain"]["mode"] == "explicit_chain" and not confirmation.confirm_legacy_chain:
            raise HTTPException(status_code=409, detail="请先确认完整审批链及旧接口不支持模板高级设置的提示。")
        item["legacy_chain_confirmed"] = confirmation.confirm_legacy_chain
    result_groups = {group["id"]: group for group in (task.get("result") or {}).get("departments", [])}
    pending = []
    for item, existing in eligible:
        group = result_groups[item["group_id"]]
        attempt = {
            "task_id": task["id"], "group_id": item["group_id"], "status": "queued",
            "instance_id": None, "error": None,
            "request_id": existing["request_id"] if existing else uuid.uuid4().hex,
            "payload": {
                "department": item["payment_department"], "amount": item["amount"], "stage": "queued",
                "submission_mode": item["legacy_chain"]["mode"],
                "confirmed_chain_fingerprint": item["legacy_chain"]["fingerprint"],
                "confirmation_fingerprint": item["confirmation_fingerprint"],
                "legacy_chain_confirmed": bool(item.get("legacy_chain_confirmed")),
                "confirmed_at": utc_now(),
            }, "updated_at": utc_now(),
        }
        db.save_attempt(attempt)
        pending.append((item, group, attempt))
    if pending:
        task.update({"status": "submitting", "updated_at": utc_now(), "error": None})
        db.save_task(task)
    return pending, len(items)

async def run_submission(
    task_id: str,
    pending: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    total: int,
    lock: asyncio.Lock,
) -> None:
    try:
        task = get_task_or_404(task_id)
        preflight = await asyncio.to_thread(dingtalk.preflight, True)
        if not preflight.get("ok"):
            raise DingTalkError("钉钉预检未通过：" + "；".join(preflight.get("messages", [])))
        schema = await asyncio.to_thread(dingtalk.get_schema)
        async def process_group(
            entry: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
        ) -> None:
            item, group, attempt = entry
            try:
                save_attempt_stage(attempt, "uploading")
                attachment = await asyncio.to_thread(
                    dingtalk.upload_approval_attachment, Path(group["file_path"])
                )
                forecast_values = form_values_from_schema(schema, item["fields"], attachment)
                form_values = form_values_from_schema(
                    schema, item["fields"], attachment, include_calculated=False
                )
                attempt["payload"].update({
                    "attachment_uploaded": bool(attachment.get("fileId") or attachment.get("file_id")),
                    "manual_actioner_count": sum(
                        len(users) for users in _node_actioners(task, item["group_id"]).values()
                    ),
                    "form_fields": form_submission_diagnostics(form_values),
                })
                save_attempt_stage(attempt, "forecasting")
                forecast = normalized_scoped_forecast(
                    await asyncio.to_thread(dingtalk.forecast, forecast_values), task, item["group_id"]
                )
                expected = (task.get("workflow_forecasts") or {}).get(
                    item["group_id"], {}
                ).get("signature", "")
                if not forecast.get("success"):
                    raise DingTalkError("提交前审批路径预测失败，未创建审批。")
                actual = forecast_signature(forecast["nodes"])
                if not expected or actual != expected:
                    raise DingTalkError("流程节点已变化，请重新检测并核对。")
                selections = _node_actioners(task, item["group_id"])
                checked = compatibility(forecast["nodes"], selections)
                errors = checked["errors"]
                actual_chain = checked["chain"]
                if actual_chain["fingerprint"] != item["legacy_chain"]["fingerprint"]:
                    errors.append("完整审批链或抄送配置发生变化，请重新预览确认。")
                if errors:
                    raise DingTalkError("；".join(errors))
                attempt["payload"].update({
                    "submit_api": DingTalkClient.submission_api(forecast["nodes"]),
                    "manual_nodes": [
                        {"name": node.get("name", ""), "actor_key": node.get("actor_key", ""),
                         "selected_count": len(selections.get(node.get("actor_key", ""), []))}
                        for node in forecast["nodes"] if node.get("kind") == "manual"
                    ],
                })
                save_attempt_stage(attempt, "creating")
                instance_id = await asyncio.to_thread(
                    dingtalk.start_process,
                    form_values,
                    forecast["nodes"],
                    selections,
                    attempt["request_id"],
                    legacy_chain_confirmed=bool(item.get("legacy_chain_confirmed")),
                    expected_chain_fingerprint=item["legacy_chain"]["fingerprint"],
                )
                save_attempt_stage(
                    attempt, "done", "submitted", instance_id=instance_id, error=None
                )
            except DingTalkUncertainSubmissionError as exc:
                save_attempt_stage(attempt, "creating", "uncertain", error=str(exc))
            except Exception as exc:
                stage = str((attempt.get("payload") or {}).get("stage") or "queued")
                save_attempt_stage(attempt, stage, "failed", error=str(exc))

        await run_limited(pending, process_group, dingtalk.batch_concurrency)
        finalize_submission(get_task_or_404(task_id), total)
    except Exception as exc:
        for _, _, original in pending:
            attempt = db.get_attempt(task_id, original["group_id"])
            if attempt and attempt.get("status") in {"queued", "submitting"}:
                stage = str((attempt.get("payload") or {}).get("stage") or "queued")
                save_attempt_stage(attempt, stage, "failed", error=str(exc))
        finalize_submission(get_task_or_404(task_id), total)
    finally:
        lock.release()

async def submit_approvals(task: dict[str, Any], failed_only: bool, confirmation: ConfirmSubmit | None = None) -> dict[str, Any]:
    lock = submission_locks.setdefault(task["id"], asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="该任务正在提交审批，请在进度窗口查看当前处理情况。",
        )
    await lock.acquire()
    try:
        current = get_task_or_404(task["id"])
        pending, total = queue_submission(current, failed_only, confirmation)
    except Exception:
        lock.release()
        raise
    if not pending:
        result = finalize_submission(get_task_or_404(task["id"]), total)
        lock.release()
        return {**result, "task": public_task(get_task_or_404(task["id"]))}
    job = asyncio.create_task(run_submission(task["id"], pending, total, lock))
    submission_jobs.add(job)

    def submission_finished(completed: asyncio.Task[Any]) -> None:
        submission_jobs.discard(completed)
        if not completed.cancelled():
            completed.exception()

    job.add_done_callback(submission_finished)
    queued_task = get_task_or_404(task["id"])
    queued_public_task = public_task(queued_task)
    return {
        "status": "submitting",
        "submitted": 0,
        "failed": 0,
        "uncertain": 0,
        "progress": queued_public_task["submission_progress"],
        "task": queued_public_task,
    }

@app.post("/api/tasks/{task_id}/approvals/submit")
async def approval_submit(task_id: str, body: ConfirmSubmit) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="正式提交前必须完成批量确认。")
    return await submit_approvals(get_task_or_404(task_id), False, body)

@app.post("/api/tasks/{task_id}/approvals/retry")
async def approval_retry(task_id: str, body: ConfirmSubmit) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="重试前必须确认。")
    return await submit_approvals(get_task_or_404(task_id), True, body)

def task_file(task_id: str, key: str) -> tuple[Path, str]:
    task = get_task_or_404(task_id)
    raw_path = (task.get("result") or {}).get(key)
    if not raw_path or not Path(raw_path).is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在或尚未生成。")
    path = Path(raw_path)
    return path, path.name

@app.get("/api/tasks/{task_id}/download/summary")
async def download_summary(task_id: str) -> FileResponse:
    path, filename = task_file(task_id, "summary")
    return FileResponse(path, filename=filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/tasks/{task_id}/download/zip")
async def download_zip(task_id: str) -> FileResponse:
    path, filename = task_file(task_id, "zip")
    return FileResponse(path, filename=filename, media_type="application/zip")

@app.get("/api/tasks/{task_id}/download/department/{index}")
async def download_department(task_id: str, index: int, group_id: str | None = None, file_name: str | None = None) -> FileResponse:
    task = get_task_or_404(task_id)
    departments = (task.get("result") or {}).get("departments", [])
    if index < 0 or index >= len(departments):
        raise HTTPException(status_code=404, detail='付款分组不存在。')
    department = departments[index]
    if (group_id and department["id"] != group_id) or (file_name and department["file_name"] != file_name):
        raise HTTPException(status_code=409, detail='附件已更新，请返回任务记录重新打开后再预览或下载。')
    path = Path(department["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail='子表文件不存在。')
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def read_department_preview(path: Path, offset: int, limit: int) -> dict[str, Any]:
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        sheet = workbook.active
        title = str(sheet.cell(1, 1).value or "")
        headers = [str(cell or "") for cell in next(sheet.iter_rows(min_row=2, max_row=2, values_only=True))]
        total = max(0, (sheet.max_row or 2) - 2)
        totals = [Decimal("0") for _ in range(3)]
        rows: list[dict[str, Any]] = []
        for number, values in enumerate(sheet.iter_rows(min_row=3, values_only=True), start=3):
            cells = list(values[:len(headers)])
            is_total = cells[0] == "合计"
            if is_total:
                for column, amount in enumerate(totals, start=6):
                    cells[column] = float(amount)
                cells[9] = float(sum(totals))
            else:
                for column in range(6, 9):
                    totals[column - 6] += Decimal(str(cells[column] or 0))
                cells[9] = float(sum(Decimal(str(cells[column] or 0)) for column in range(6, 9)))
            if offset <= number - 3 < offset + limit:
                rows.append({"number": number, "cells": [
                    None if value is None else value if isinstance(value, (str, int, float, bool)) else str(value)
                    for value in cells
                ]})
        return {"file_name": path.name, "sheet_name": sheet.title, "title": title,
                "headers": headers, "total": total, "offset": offset, "limit": limit, "rows": rows}
    finally:
        workbook.close()

@app.get("/api/tasks/{task_id}/preview/department/{index}")
async def preview_department(task_id: str, index: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), group_id: str | None = None, file_name: str | None = None) -> dict[str, Any]:
    task = get_task_or_404(task_id)
    departments = (task.get("result") or {}).get("departments", [])
    if index < 0 or index >= len(departments):
        raise HTTPException(status_code=404, detail='付款分组不存在。')
    department = departments[index]
    if (group_id and department["id"] != group_id) or (file_name and department["file_name"] != file_name):
        raise HTTPException(status_code=409, detail='附件已更新，请返回任务记录重新打开后再预览或下载。')
    path = Path(department["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail='子表文件不存在。')
    return await asyncio.to_thread(read_department_preview, path, offset, limit)

@app.post("/api/tasks/{task_id}/download/selected")
async def download_selected(task_id: str, body: SelectedDownloads) -> StreamingResponse:
    task = get_task_or_404(task_id)
    departments = (task.get("result") or {}).get("departments", [])
    indices = list(dict.fromkeys(body.indices))
    if not indices:
        raise HTTPException(status_code=400, detail="请至少选择一个部门明细文件。")
    if any(index < 0 or index >= len(departments) for index in indices):
        raise HTTPException(status_code=400, detail="选中的部门文件不存在，请刷新后重试。")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for index in indices:
            path = Path(departments[index]["file_path"])
            if not path.is_file():
                raise HTTPException(status_code=404, detail=f"子表文件不存在：{path.name}")
            output.write(path, arcname=path.name)
    archive.seek(0)
    month = str((task.get("result") or {}).get("month") or "当月")
    filename = f"{month}已选部门补贴明细.zip"
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )

frontend_dist = ROOT_DIR / "web"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


@app.middleware("http")
async def prevent_experiment_submission(request, call_next):
    if request.url.path.startswith("/api/tasks/") and request.url.path.endswith(("/approvals/submit", "/approvals/retry")):
        return JSONResponse(status_code=409, content={"detail": BLOCK_MESSAGE, "read_only": True})
    return await call_next(request)

@app.get("/api/experiment/replay")
async def replay_diagnostic_sample(export: bool = Query(False)):
    report = await asyncio.to_thread(historical_replay, ROOT_DIR)
    safe = redact(report)
    return JSONResponse(safe, headers={"Content-Disposition": 'attachment; filename="historical-diagnostics.json"'} if export else {})

def _query_task_diagnostics(task):
    # This path deliberately never calls save_task, record_attempt or any upload/create method.
    preview, _ = preview_items(task)
    schema_error = None
    query_failure = None
    try:
        schema = dingtalk.get_schema()
    except Exception:
        schema = None
        schema_error = "模板查询失败或无权限，本次未能验证表单和最新流程。"
        query_failure = dingtalk.last_failure
    reports = []
    access_issue = None
    access_failure = None
    if schema is not None:
        try:
            access = dingtalk.originator_process_access()
            if access.get("allowed") is False:
                access_issue = {"code": "ORIGINATOR_ACCESS", "level": "error", "field": "发起权限", "message": "钉钉权限查询返回该发起人不可发起当前模板。"}
        except Exception:
            access_failure = dingtalk.last_failure
            access_issue = {"code": "ORIGINATOR_ACCESS_UNVERIFIED", "level": "unverified", "field": "发起权限", "message": "权限查询失败或无权限，未验证发起资格。"}
    for item in preview:
        group_failure = query_failure or access_failure
        report = item["diagnostics"]
        stored = (task.get("workflow_forecasts") or {}).get(item["group_id"], {})
        if schema is None:
            report = item["diagnostics"]
            report["findings"].append({"code": "SCHEMA_QUERY_UNVERIFIED", "level": "unverified", "field": "模板", "message": schema_error})
        else:
            try:
                fields = item["fields"]
                draft = payload_for_preview(dingtalk, schema, fields, stored.get("nodes", []), item["node_actioners"])
                report = build_report(schema, draft, stored, item["node_actioners"], expected_amount=item["amount"], pending_attachment=True)
                fields = resolve_display_fields_from_schema(schema, fields)
                values = form_values_from_schema(schema, fields)
                fresh = normalized_scoped_forecast(dingtalk.forecast(values), task, item["group_id"])
                payload = payload_for_preview(dingtalk, schema, fields, fresh["nodes"], item["node_actioners"])
                report = build_report(schema, payload, fresh, item["node_actioners"], client=dingtalk,
                                      expected_amount=item["amount"], previous_nodes=stored.get("nodes") if stored else None,
                                      local_errors=item["errors"], pending_attachment=True)
            except Exception:
                group_failure = dingtalk.last_failure or group_failure
                report["findings"].append({"code": "FORECAST_QUERY_UNVERIFIED", "level": "unverified", "field": "流程", "message": "最新流程或表单查询未完成，不能确认节点是否变化；未修改已保存选择。"})
        snapshot = stored.get("schema_snapshot")
        if snapshot and schema and fingerprint({k: snapshot.get(k) for k in ("formCode", "schemaContent", "status")}) != fingerprint({k: schema.get(k) for k in ("formCode", "schemaContent", "status")}):
            report["findings"].append({"code": "TEMPLATE_CHANGED", "level": "error", "field": "模板", "message": "模板表单定义或状态已变化，请重新检测、核对并生成预览。"})
        if group_failure:
            report["failure"] = failure_record(group_failure, origin="钉钉本次只读查询响应")
        if access_issue: report["findings"].append(access_issue)
        report["diagnostic_summary"] = summarize(report)
        report["structural_errors"] = report["diagnostic_summary"]["local_errors"]
        report["unverified_count"] = report["diagnostic_summary"]["unverified"]
        reports.append({"group_id": item["group_id"], "company": item["company"], "payment_department": item["payment_department"],
                        "diagnostics": report, "diagnostic_summary": report["diagnostic_summary"]})
    return {"task_id": task["id"], "task_status": task["status"], "read_only": True, "items": reports,
            "note": "仅查询与诊断，任务状态、人员选择及提交记录均未改变。"}

@app.post("/api/tasks/{task_id}/approvals/diagnostics")
async def approval_diagnostics(task_id: str, export: bool = Query(False)):
    task = get_task_or_404(task_id)
    if not task.get("result"):
        raise HTTPException(status_code=409, detail="请先在实验副本中生成本地付款附件。")
    result = await asyncio.to_thread(_query_task_diagnostics, task)
    safe = redact(result)
    return JSONResponse(safe, headers={"Content-Disposition": 'attachment; filename="task-diagnostics.json"'} if export else {})
