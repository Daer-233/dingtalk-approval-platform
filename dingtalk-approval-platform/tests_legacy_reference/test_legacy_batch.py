from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from app.db import Database
from app.dingtalk import DingTalkConfig, DingTalkError, DingTalkUncertainSubmissionError, forecast_signature
from test_manual_submission import fixture


class LegacyBatchIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """Temporary SQLite and fully mocked DingTalk client: no live approvals."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"APP_DATA_DIR": self.temp.name}):
            self.main = importlib.import_module("app.main")
        self.db = Database(Path(self.temp.name) / "test.db")
        p = patch.object(self.main, "db", self.db); p.start(); self.addCleanup(p.stop)
        self.nodes, self.selections = fixture()
        config = DingTalkConfig(agent_id="123", process_code="TEST", originator_user_id="u",
                               originator_dept_id="456", live_submit_enabled=True)
        self.client = Mock(config=config, batch_concurrency=3)
        self.client.preflight.return_value = {"ok": True}
        self.client.get_schema.return_value = {}
        self.client.upload_approval_attachment.return_value = {"fileId": "fake-attachment"}
        self.client.forecast.return_value = {"success": True, "nodes": self.nodes}
        self.client.start_process.return_value = "fake-instance"
        p = patch.object(self.main, "dingtalk", self.client); p.start(); self.addCleanup(p.stop)
        p = patch.object(self.main, "normalized_scoped_forecast", side_effect=lambda x: x); p.start(); self.addCleanup(p.stop)
        p = patch.object(self.main, "form_values_from_schema", return_value=[]); p.start(); self.addCleanup(p.stop)
        self.db.save_profile({"company": "test-company", "payment_entity": "payer", "supplier_name": "supplier",
                              "bank_account": "fake-bank", "bank_name": "bank", "updated_at": self.main.utc_now()})
        now = self.main.utc_now()
        groups = [{"id": f"g{i}", "company": "test-company", "payment_department": f"dept{i}", "count": 1,
                   "amount": 100, "file_name": f"test{i}.xlsx", "file_path": f"test{i}.xlsx"} for i in range(2)]
        self.task = {"id": "batch-test", "status": "previewed", "file_name": "test.xlsx", "created_at": now,
                     "updated_at": now, "source_path": "fake.xlsx", "output_root": "fake", "error": None,
                     "result": {"month": "9月", "departments": groups},
                     "workflow_forecasts": {g["id"]: {"nodes": deepcopy(self.nodes), "signature": forecast_signature(self.nodes)} for g in groups},
                     "workflow_actioners": {g["id"]: deepcopy(self.selections) for g in groups}}
        self.task["previews"], valid = self.main.preview_items(self.task)
        self.assertTrue(valid)
        self.db.save_task(self.task)
        self.stages = {g["id"]: [] for g in groups}
        original = self.db.save_attempt
        def save(attempt):
            self.stages[attempt["group_id"]].append((attempt["payload"]["stage"], attempt["status"]))
            original(attempt)
        p = patch.object(self.db, "save_attempt", side_effect=save); p.start(); self.addCleanup(p.stop)

    async def run_batch(self, retry=False):
        task = self.db.get_task(self.task["id"])
        confirmation = self.main.ConfirmSubmit(confirm=True, confirm_legacy_chain=True,
            preview_fingerprints={p["group_id"]: p["confirmation_fingerprint"] for p in task["previews"]})
        pending, total = self.main.queue_submission(task, retry, confirmation)
        lock = asyncio.Lock(); await lock.acquire()
        await self.main.run_submission(task["id"], pending, total, lock)
        self.assertFalse(lock.locked())
        return self.db.approval_attempts(task["id"])

    async def test_full_batch_stages_persist_and_restart_reads_confirmed_chain(self):
        attempts = await self.run_batch()
        self.assertEqual(self.client.preflight.call_count, 1)
        self.assertEqual(self.client.get_schema.call_count, 1)
        for group in self.stages:
            self.assertEqual(self.stages[group], [("queued", "queued"), ("uploading", "submitting"),
                ("forecasting", "submitting"), ("creating", "submitting"), ("done", "submitted")])
        reloaded = Database(self.db.path).approval_attempts(self.task["id"])
        self.assertEqual(len(reloaded), 2)
        for attempt in reloaded:
            self.assertEqual(attempt["status"], "submitted")
            self.assertTrue(attempt["payload"]["legacy_chain_confirmed"])
            self.assertTrue(attempt["payload"]["confirmed_chain_fingerprint"])
        self.assertEqual(self.db.get_task(self.task["id"])["status"], "submitted")
        for call in self.client.start_process.call_args_list:
            self.assertTrue(call.kwargs["legacy_chain_confirmed"])
            self.assertTrue(call.kwargs["expected_chain_fingerprint"])

    async def test_changed_post_upload_path_blocks_all_create_calls(self):
        changed = deepcopy(self.nodes)
        changed[1]["fixed_actioners"] = [{"user_id": "different-user", "name": "different"}]
        self.client.forecast.return_value = {"success": True, "nodes": changed}
        attempts = await self.run_batch()
        self.client.start_process.assert_not_called()
        self.assertTrue(all(a["status"] == "failed" for a in attempts))
        self.assertTrue(all("forecasting" == a["payload"]["stage"] for a in attempts))

    async def test_failure_isolated_and_retry_never_resubmits_success(self):
        def create(*args, **kwargs):
            if args[3] == self.db.get_attempt(self.task["id"], "g0")["request_id"]:
                raise DingTalkError("simulated explicit failure")
            return "fake-success"
        self.client.start_process.side_effect = create
        attempts = await self.run_batch()
        self.assertEqual({a["group_id"]: a["status"] for a in attempts}, {"g0": "failed", "g1": "submitted"})
        self.assertEqual(self.db.get_task(self.task["id"])["status"], "partial_failed")
        self.client.start_process.side_effect = None
        self.client.start_process.return_value = "fake-retry-success"
        await self.run_batch(retry=True)
        self.assertEqual(self.client.start_process.call_count, 3)
        self.assertEqual(self.db.get_attempt(self.task["id"], "g1")["instance_id"], "fake-success")

    async def test_create_timeout_uncertain_and_not_retried(self):
        self.client.start_process.side_effect = DingTalkUncertainSubmissionError("simulated timeout")
        attempts = await self.run_batch()
        self.assertTrue(all(a["status"] == "uncertain" for a in attempts))
        await self.run_batch(retry=True)
        self.assertEqual(self.client.start_process.call_count, 2)


if __name__ == "__main__":
    unittest.main()
