from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app.db import Database
from app.dingtalk import DingTalkError, apply_local_role_scopes, validate_node_actioners


class PickerCandidatesTest(unittest.TestCase):
    """Temporary SQLite and mocked directory: never creates a real approval."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"APP_DATA_DIR": self.temp.name}):
            self.main = importlib.import_module("app.main")
        self.db = Database(Path(self.temp.name) / "test.db")
        self.dingtalk = Mock()
        for name, value in (("db", self.db), ("dingtalk", self.dingtalk)):
            replacement = patch.object(self.main, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(self.main.app)
        self.addCleanup(self.client.close)
        self.default = self.main.DEFAULT_APPROVAL_ROLE_SCOPES[0]
        self.existing = {**self.default, "users": [{"user_id": "old", "name": "原人员"}], "updated_at": self.main.utc_now()}
        self.db.save_approval_role_scope(self.existing)
        self.dingtalk.users_by_ids.return_value = [{"user_id": "new", "name": "通讯录真实姓名"}]
        self.url = "/api/approval-role-scopes/candidates"
        self.body = {"role_name": self.default["role_name"], "users": [{"user_id": "new", "name": "不可信任的客户端姓名"}]}

    def tearDown(self):
        self.dingtalk.start_process.assert_not_called()
        self.dingtalk.upload_approval_attachment.assert_not_called()

    def test_default_node_append_keeps_old_candidates_and_verified_name(self):
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        scope = response.json()
        self.assertEqual(scope["role_key"], self.default["role_key"])
        self.assertEqual(scope["users"], [{"user_id": "old", "name": "原人员"}, {"user_id": "new", "name": "通讯录真实姓名"}])
        self.assertEqual(self.db.list_approval_role_scopes()[0]["users"], scope["users"])

    def test_repeat_append_is_idempotent(self):
        first = self.client.post(self.url, json=self.body)
        second = self.client.post(self.url, json=self.body)
        self.assertEqual(first.json()["users"], second.json()["users"])
        self.assertEqual(len(self.db.list_approval_role_scopes()), 1)

    def test_custom_node_normalized_name_merges_into_existing_scope(self):
        body = {**self.body, "role_name": "自选／节点"}
        first = self.client.post(self.url, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.dingtalk.users_by_ids.return_value = [{"user_id": "next", "name": "第二人"}]
        body = {"role_name": " 自选 / 节点 ", "users": [{"user_id": "next", "name": "第二人"}]}
        second = self.client.post(self.url, json=body)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["role_key"], second.json()["role_key"])
        self.assertEqual([u["user_id"] for u in second.json()["users"]], ["new", "next"])

    def test_unknown_directory_person_blocks_entire_append(self):
        self.dingtalk.users_by_ids.return_value = []
        before = self.db.list_approval_role_scopes()
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.db.list_approval_role_scopes(), before)

    def test_directory_failure_does_not_edit_scope(self):
        self.dingtalk.users_by_ids.side_effect = DingTalkError("授权范围错误")
        before = self.db.list_approval_role_scopes()
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.db.list_approval_role_scopes(), before)

    def test_empty_candidates_rejected(self):
        response = self.client.post(self.url, json={**self.body, "users": []})
        self.assertEqual(response.status_code, 422)
        self.dingtalk.users_by_ids.assert_not_called()

    def test_legacy_custom_route_still_rejects_reserved_default_roles(self):
        response = self.client.post("/api/approval-role-scopes/custom", json=self.body)
        self.assertEqual(response.status_code, 400, response.text)
        self.dingtalk.users_by_ids.assert_not_called()

    def test_appended_users_cannot_widen_template_allowance(self):
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        nodes = [{"kind": "manual", "name": self.default["role_name"], "actor_key": "k", "required": True,
                  "allowed_multi": False, "allowed_user_ids": ["old"], "selection_range_kind": "users"}]
        apply_local_role_scopes(nodes, [response.json()])
        self.assertEqual(nodes[0]["template_allowed_user_ids"], ["old"])
        self.assertEqual(nodes[0]["allowed_user_ids"], ["old"])
        self.assertTrue(validate_node_actioners(nodes, {"k": [{"user_id": "new"}]}))

    def test_appended_user_selectable_when_template_has_no_explicit_range(self):
        response = self.client.post(self.url, json=self.body)
        nodes = [{"kind": "manual", "name": self.default["role_name"], "actor_key": "k", "required": True,
                  "allowed_multi": False, "allowed_user_ids": [], "selection_range_kind": "unrestricted"}]
        apply_local_role_scopes(nodes, [response.json()])
        self.assertEqual(nodes[0]["allowed_user_ids"], ["old", "new"])
        self.assertEqual(validate_node_actioners(nodes, {"k": [{"user_id": "new"}]}), [])


if __name__ == "__main__":
    unittest.main()
