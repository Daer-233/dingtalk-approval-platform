from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app.db import Database
from app.dingtalk import DingTalkConfig, forecast_signature
from modern_test_fixture import fixture


class PreviewRoutesTest(unittest.TestCase):
    """Browser HTTP requests with temporary SQLite and no live DingTalk calls."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"APP_DATA_DIR": self.temp.name}):
            self.main = importlib.import_module("app.main")
        self.db = Database(Path(self.temp.name) / "test.db")
        self.nodes, self.selections = fixture()
        self.dingtalk = Mock(config=DingTalkConfig(
            agent_id="123", process_code="TEST", originator_user_id="test-user",
            originator_dept_id="456", live_submit_enabled=False))
        self.dingtalk.public_config.return_value = {"live_submit_enabled": False}
        for attribute, value in (("db", self.db), ("dingtalk", self.dingtalk),
                                 ("submission_locks", {})):
            replacement = patch.object(self.main, attribute, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(self.main.app)
        self.addCleanup(self.client.close)
        now = self.main.utc_now()
        self.profile = {"company": "test-company", "payment_entity": "payer",
                        "supplier_name": "supplier", "bank_account": "fake-bank",
                        "bank_name": "bank"}
        self.db.save_profile({**self.profile, "updated_at": now})
        self.task = {
            "id": "preview-route-test", "status": "prepared", "file_name": "test.xlsx",
            "created_at": now, "updated_at": now, "source_path": "fake.xlsx",
            "output_root": "fake", "error": None,
            "result": {"month": "9月", "departments": [{
                "id": "g1", "company": self.profile["company"], "payment_department": "test-dept",
                "count": 1, "amount": 120, "file_name": "test-dept.xlsx",
                "file_path": "test-dept.xlsx"}]},
            "workflow_forecasts": {"g1": {"nodes": deepcopy(self.nodes),
                "signature": forecast_signature(self.nodes)}},
            "workflow_actioners": {}, "previews": []}
        self.db.save_task(self.task)
        self.actioners_url = f"/api/tasks/{self.task['id']}/approvals/actioners"
        self.preview_url = f"/api/tasks/{self.task['id']}/approvals/preview"

    def tearDown(self):
        self.dingtalk.start_process.assert_not_called()
        self.dingtalk.upload_approval_attachment.assert_not_called()
        self.dingtalk.get_schema.assert_not_called()

    def test_browser_save_then_preview_persists_selected_people(self):
        saved = self.client.put(self.actioners_url, json={"groups": {"g1": self.selections}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["groups"]["g1"], self.selections)
        task = self.db.get_task(self.task["id"])
        self.assertEqual(task["workflow_actioners"]["g1"], self.selections)
        self.assertEqual(task["previews"], [])
        preview = self.client.post(self.preview_url, json={})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["valid"], preview.text)
        item = preview.json()["items"][0]
        self.assertEqual(item["node_actioners"], self.selections)
        self.assertEqual(item["amount"], 120)
        self.assertTrue(item["confirmation_fingerprint"])
        self.assertEqual(item["legacy_chain"]["mode"], "modern_template")
        task = self.db.get_task(self.task["id"])
        self.assertEqual(task["status"], "previewed")
        self.assertEqual(task["previews"][0]["node_actioners"], self.selections)

    def test_stale_prediction_blocks_before_upload_with_actionable_message(self):
        task = self.db.get_task(self.task["id"])
        nodes = task["workflow_forecasts"]["g1"]["nodes"]
        for node in nodes:
            node["metadata_version"] = 2
        task["workflow_forecasts"]["g1"]["signature"] = forecast_signature(nodes)
        task["workflow_actioners"] = {"g1": deepcopy(self.selections)}
        self.db.save_task(task)
        response = self.client.post(self.preview_url, json={})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["valid"])
        self.assertTrue(any("旧版流程预测" in error for error in response.json()["items"][0]["errors"]))
        from dataclasses import replace
        self.dingtalk.config = replace(self.dingtalk.config, live_submit_enabled=True)
        task = self.db.get_task(task["id"])
        confirmation = self.main.ConfirmSubmit(confirm=True,
            preview_fingerprints={p["group_id"]: p["confirmation_fingerprint"] for p in task["previews"]})
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            self.main.queue_submission(task, False, confirmation)
        self.assertIn("重新检测审批路径", caught.exception.detail)
        self.assertEqual(self.db.approval_attempts(task["id"]), [])

    def test_unforecast_group_rejected_without_writing(self):
        before = self.db.get_task(self.task["id"])
        result = self.client.put(self.actioners_url, json={"groups": {"unknown": self.selections}})
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.db.get_task(self.task["id"]), before)

    def test_missing_required_people_rejected_without_writing(self):
        before = self.db.get_task(self.task["id"])
        result = self.client.put(self.actioners_url, json={"groups": {"g1": {}}})
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.db.get_task(self.task["id"]), before)

    def test_missing_task_returns_handler_error_not_unregistered_route(self):
        result = self.client.put("/api/tasks/missing/approvals/actioners", json={"groups": {}})
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.json()["detail"], "任务不存在。")

    def test_running_task_returns_conflict_without_writing(self):
        lock = asyncio.Lock()
        asyncio.run(lock.acquire())
        self.addCleanup(lock.release)
        self.main.submission_locks[self.task["id"]] = lock
        before = self.db.get_task(self.task["id"])
        for method, url, body in (
            ("put", self.actioners_url, {"groups": {"g1": self.selections}}),
            ("post", self.preview_url, {})):
            result = getattr(self.client, method)(url, json=body)
            self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.db.get_task(self.task["id"]), before)

    def test_company_profile_put_route_saves_payment_information(self):
        result = self.client.put("/api/company-profiles/test-company", json=self.profile)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.db.get_profile("test-company")["payment_entity"], "payer")
        mismatch = self.client.put("/api/company-profiles/another-company", json=self.profile)
        self.assertEqual(mismatch.status_code, 400, mismatch.text)
        self.assertIsNone(self.db.get_profile("another-company"))


if __name__ == "__main__":
    unittest.main()
