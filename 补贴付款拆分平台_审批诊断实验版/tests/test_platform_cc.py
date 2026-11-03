from __future__ import annotations

import importlib

import os

import tempfile

import unittest

from copy import deepcopy

from pathlib import Path

from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.approval_cc import seed_cc_nodes, compatibility

from app.db import Database

from app.dingtalk import DingTalkConfig, forecast_signature
from app.platform_dingtalk import normalize_forecast, validate_node_actioners

from app.platform_paths import CHAIN_VERSION

from modern_test_fixture import node

class CCSeedingTest(unittest.TestCase):
    def cc(self, **extra):
        return node("cc", "approve", manual=True, role="notifier", users=["template"], required=False,
                    allowed_multi=True, allowed_user_ids=[], selection_range_kind="missing",
                    selection_range_restricted=False, selection_range_resolvable=False, **extra)

    def test_merge_dedup_sources_and_template_cannot_be_removed(self):
        n = self.cc(); snapshot = {"users": [{"user_id": "template", "name": "重复"}, {"user_id": "local", "name": "本地"}]}
        selected = seed_cc_nodes([n], snapshot, {})
        self.assertEqual([u["user_id"] for u in selected["cc"]], ["template", "local"])
        self.assertEqual(selected["cc"][0]["source"], "template")
        self.assertEqual(selected["cc"][1]["source"], "local_default")
        self.assertEqual(validate_node_actioners([n], selected), [])
        self.assertTrue(validate_node_actioners([n], {"cc": [{"user_id": "local"}]}))

    def test_explicit_per_group_adjustment_and_empty_selection_retained(self):
        n = self.cc(); selected = seed_cc_nodes([n], {"users": [{"user_id": "local", "name": "本地"}]}, {"cc": []})
        self.assertEqual([u["user_id"] for u in selected["cc"]], ["template"])
        n2 = self.cc(); n2["fixed_actioners"] = []
        self.assertEqual(seed_cc_nodes([n2], {"users": [{"user_id": "local"}]}, {"cc": []})["cc"], [])

    def test_explicit_template_range_cannot_be_widened_or_silently_drop_default(self):
        n = self.cc(); n.update(allowed_user_ids=["template"], selection_range_kind="users",
                              selection_range_restricted=True, selection_range_resolvable=True)
        selected = seed_cc_nodes([n], {"users": [{"user_id": "outside"}]}, {})
        self.assertEqual(n["allowed_user_ids"], ["template"])
        self.assertEqual(len(selected["cc"]), 2)
        self.assertTrue(validate_node_actioners([n], selected))

    def test_unresolved_range_is_reported_and_no_cc_node_is_invented(self):
        n = self.cc(); n["selection_range_kind"] = "unsupported"
        seed_cc_nodes([n], {"users": [{"user_id": "local"}]}, {})
        self.assertTrue(n["cc_default_errors"])
        fixed = node("cc", "cc", role="notifier")
        self.assertEqual(seed_cc_nodes([fixed], {"users": [{"user_id": "local"}]}, {}), {})
        self.assertEqual(fixed["fixed_actioners"][0]["user_id"], "fixed-cc")

class CCDefaultsAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"APP_DATA_DIR": self.temp.name}): self.main = importlib.import_module("app.main")
        self.db = Database(Path(self.temp.name) / "cc.db")
        self.ding = Mock(config=DingTalkConfig(corp_id="corp-a", process_code="test-template", applicant_dept_id="1"))
        self.ding.users_by_ids.side_effect = lambda ids: [{"user_id": key, "name": "verified-" + key} for key in ids if key != "unauthorized"]
        for key, value in [("db", self.db), ("dingtalk", self.ding)]:
            p = patch.object(self.main, key, value); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(self.main.app); self.addCleanup(self.client.close)
        self.body = {"corp_id": "corp-a", "process_code": "test-template", "users": [{"user_id": "u", "name": "untrusted"}]}

    def tearDown(self):
        self.ding.start_process.assert_not_called()
        self.ding.upload_approval_attachment.assert_not_called()

    def test_verified_names_persist_restart_and_scopes_do_not_expose_defaults(self):
        response = self.client.put("/api/approval-cc-defaults", json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["users"], [{"user_id": "u", "name": "verified-u"}])
        self.assertEqual(Database(self.db.path).cc_defaults("corp-a", "test-template")["users"], response.json()["users"])
        self.assertEqual(self.db.list_approval_role_scopes(), [])
        self.assertTrue(all(not i["role_key"].startswith("@") for i in self.client.get("/api/approval-role-scopes").json()["items"]))

    def test_duplicate_invalid_and_unauthorized_person_reject_without_writing(self):
        for ids in [["u", "u"], [""], ["a,b"], ["unauthorized"]]:
            body = {**self.body, "users": [{"user_id": key} for key in ids]}
            self.assertEqual(self.client.put("/api/approval-cc-defaults", json=body).status_code, 400)
        self.assertEqual(self.client.get("/api/approval-cc-defaults").json()["users"], [])

    def test_corp_and_template_isolation_stale_put_and_reserved_scope_protection(self):
        self.client.put("/api/approval-cc-defaults", json=self.body)
        for corp, process in [("corp-b", "test-template"), ("corp-a", "template-b")]:
            self.assertEqual(self.db.cc_defaults(corp, process)["users"], [])
            body = {**self.body, "corp_id": corp, "process_code": process}
            self.assertEqual(self.client.put("/api/approval-cc-defaults", json=body).status_code, 409)
        response = self.client.put("/api/approval-role-scopes", json={"scopes": [{"role_key": "@cc:reserved", "role_name": "bad"}]})
        self.assertEqual(response.status_code, 400)

    def test_prepare_snapshots_config_then_changes_do_not_modify_task(self):
        self.client.put("/api/approval-cc-defaults", json=self.body)
        task = {"id": "test-task", "status": "review", "file_name": "dummy.xlsx", "source_path": "dummy.xlsx", "output_root": "dummy", "created_at": "now", "updated_at": "now", "choices": [{"group_id": "g", "payment_department": "部门", "selected": True}]}
        self.db.save_task(task)
        with patch.object(self.main, "generate_selected_payment_files", return_value={"departments": []}):
            response = self.client.post("/api/tasks/test-task/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()["cc_defaults_snapshot"]
        self.client.put("/api/approval-cc-defaults", json={**self.body, "users": []})
        self.assertEqual(self.db.get_task("test-task")["cc_defaults_snapshot"], snapshot)
        with patch.object(self.main, "generate_selected_payment_files", return_value={"departments": []}):
            self.client.post("/api/tasks/test-task/prepare")
        self.assertEqual(self.db.get_task("test-task")["cc_defaults_snapshot"]["users"], [])

    def test_forecast_api_persists_seed_and_keeps_deliberate_group_choices(self):
        self.client.put("/api/approval-cc-defaults", json=self.body)
        group = {"id": "g", "company": "test-company", "payment_department": "test-department", "amount": 123, "count": 1, "file_name": "mock.xlsx"}
        task = {"id": "cc-task", "status": "prepared", "file_name": "mock.xlsx", "source_path": "mock.xlsx", "output_root": "mock", "created_at": "now", "updated_at": "now", "result": {"month": "9月", "departments": [group]}, "workflow_actioners": {}, "cc_defaults_snapshot": self.main.current_cc_defaults()}
        self.db.save_task(task)
        raw = {"isForecastSuccess": True, "workflowActivityRules": [
            {"activityId": "approve", "activityName": "角色审批", "prevActivityId": "start", "activityType": "target_label", "activityActioners": [{"userId": "approved-person", "name": "审批人"}], "workflowActor": {"actorType": "approver", "approvalType": "MANUAL", "approvalMethod": "OR", "actorActivateType": "ALL"}},
            {"activityId": "cc", "activityName": "抄送人", "prevActivityId": "approve", "activityType": "target_approval", "isTargetSelect": True, "activityActioners": [{"userId": "template-person", "name": "模板人员"}], "workflowActor": {"actorType": "notifier", "actorKey": "cc-key", "required": False, "allowedMulti": True}}
        ], "workflowForecastNodes": [{"activityId": "start", "outId": "approve"}, {"activityId": "approve", "outId": "cc"}, {"activityId": "cc"}]}
        self.ding.forecast.return_value = raw
        self.ding.get_schema.return_value = {}
        with patch.object(self.main, "form_values_from_schema", return_value=[]), patch.object(self.main, "resolve_display_fields_from_schema", side_effect=lambda schema,fields: fields):
            response = self.client.post("/api/tasks/cc-task/approvals/forecast", json={})
            self.assertEqual(response.status_code, 200, response.text)
            returned = response.json()["groups"][0]
            self.assertIsNone(returned["error"])
            self.assertEqual([u["user_id"] for u in returned["node_actioners"]["cc-key"]], ["template-person", "u"])
            self.assertEqual(self.db.get_task("cc-task")["workflow_actioners"]["g"]["cc-key"][0]["source"], "template")
            self.assertEqual(returned["node_actioners"]["cc-key"][1].get("source"), "local_default")
            self.assertEqual(returned["compatibility"]["status"], "submittable")
            # No actioner mutation endpoint may remove immutable template people.
            before = self.db.get_task("cc-task")["workflow_actioners"]
            rejected = self.client.put("/api/tasks/cc-task/approvals/actioners", json={"groups": {"g": {"cc-key": [{"user_id": "u"}]}}})
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(self.db.get_task("cc-task")["workflow_actioners"], before)
            # Global changes must not replace the snapshot or a deliberately empty local choice.
            self.client.put("/api/approval-cc-defaults", json={**self.body, "users": [{"user_id": "new-person"}]})
            saved = self.client.put("/api/tasks/cc-task/approvals/actioners", json={"groups": {"g": {"cc-key": [{"user_id": "template-person", "name": "模板人员"}]}}})
            self.assertEqual(saved.status_code, 200, saved.text)
            reforecast = self.client.post("/api/tasks/cc-task/approvals/forecast", json={})
            self.assertEqual([u["user_id"] for u in reforecast.json()["groups"][0]["node_actioners"]["cc-key"]], ["template-person"])
            restored = Database(self.db.path).get_task("cc-task")
            self.assertEqual(restored["cc_defaults_snapshot"]["users"][0]["user_id"], "u")

class ModernAdapterRegressionTest(unittest.TestCase):
    def test_confirmation_is_stable_and_matches_unchanged_core(self):
        from modern_test_fixture import fixture
        from app.modern_chain import build_modern_template_chain
        nodes, selections = fixture()
        first = compatibility(nodes, selections)["chain"]["fingerprint"]
        second = compatibility(nodes, selections)["chain"]["fingerprint"]
        self.assertEqual(first, second)
        self.assertEqual(first, build_modern_template_chain(nodes, selections)["fingerprint"])
        selections["director"] = [{"user_id": "u2", "name": "user2"}]
        self.assertNotEqual(first, compatibility(nodes, selections)["chain"]["fingerprint"])

    def test_new_platform_adapter_inherits_all_api_operations(self):
        from app.dingtalk import DingTalkClient
        from app.platform_dingtalk import PlatformDingTalkClient
        for name in ("prepare_start_payload", "start_process", "forecast", "upload_approval_attachment", "_request"):
            self.assertIs(getattr(PlatformDingTalkClient, name), getattr(DingTalkClient, name))
