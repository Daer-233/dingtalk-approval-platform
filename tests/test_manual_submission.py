from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from app.dingtalk import (DingTalkClient, DingTalkConfig, DingTalkError,
    DingTalkUncertainSubmissionError, normalize_forecast, forecast_signature,
    validate_node_actioners)
from app.legacy_chain import CHAIN_VERSION, build_legacy_chain, order_forecast_path


def node(key, prev, manual=False, users=None, role="approver", **changes):
    value = {
        "activity_id": key, "prev_activity_id": prev, "metadata_version": CHAIN_VERSION,
        "name": key, "kind": "manual" if manual else "fixed",
        "activity_type": "target_select" if manual else "target_approval",
        "actor_type": role, "approval_type": "MANUAL", "actioners_complete": True,
        "actor_key": key if manual else "", "required": manual,
        "allowed_multi": False, "allowed_user_ids": ["u1", "u2", "u3"],
        "selection_range_restricted": True, "selection_range_resolvable": True,
        "approval_method": "ONE_BY_ONE", "activation_type": "ONE_BY_ONE",
        "fixed_actioners": [{"user_id": uid, "name": uid} for uid in (users or ["fixed-" + key])],
    }
    value.update(changes)
    return value


def fixture():
    nodes = [node("cc-start", "cc-start", users=["cc"], role="notifier"),
             node("fixed", "cc-start"), node("director", "fixed", True),
             node("manager", "director", True), node("department", "manager", True),
             node("accountant", "department"), node("finance", "accountant"),
             node("cashier", "finance"), node("cc-end", "cashier", users=["cc"], role="notifier")]
    selections = {n["actor_key"]: [{"user_id": "u" + str(i), "name": "user" + str(i)}]
                  for i, n in enumerate([n for n in nodes if n["kind"] == "manual"], 1)}
    return nodes, selections


class ManualSubmissionTest(unittest.TestCase):
    """All HTTP uses MockTransport: cannot create real approvals."""
    def setUp(self):
        self.client = DingTalkClient(DingTalkConfig(agent_id="123", process_code="TEST-PROC",
            originator_user_id="test-originator", originator_dept_id="456", live_submit_enabled=True))
        self.client.access_token = lambda: "test-token"
        self.requests = []
        self.nodes, self.selections = fixture()
        self.fixed = [node("fixed", "fixed")]
        self.addCleanup(self.client.close)

    def transport(self, status=200, data=None, error=None):
        def handler(request):
            self.requests.append(request)
            if error:
                raise error("simulated failure", request=request)
            return httpx.Response(status, json=data or {})
        self.client._http.close()
        self.client._http = httpx.Client(transport=httpx.MockTransport(handler), timeout=30)

    def submit(self, nodes=None, selections=None, **kw):
        nodes = nodes if nodes is not None else self.nodes
        selections = selections if selections is not None else self.selections
        chain = build_legacy_chain(nodes, selections)
        return self.client.start_process([], nodes, selections, "local-request",
            legacy_chain_confirmed=kw.get("consent", True),
            expected_chain_fingerprint=kw.get("fingerprint", chain["fingerprint"]))

    def test_full_chain_preserves_manual_fixed_finance_and_cc(self):
        self.transport(data={"process_instance_id": "test-instance"})
        original = deepcopy(self.selections)
        self.assertEqual(self.submit(), "test-instance")
        self.assertEqual(len(self.requests), 1)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["approvers_v2"], [
            {"task_action_type": "NONE", "user_ids": [uid]} for uid in
            ["fixed-fixed", "u1", "u2", "u3", "fixed-accountant", "fixed-finance", "fixed-cashier"]])
        self.assertEqual(payload["cc_list"], "cc")
        self.assertEqual(payload["cc_position"], "START_FINISH")
        self.assertNotIn("target_select_actioners", payload)
        self.assertNotIn("approvers", payload)
        self.assertEqual(self.requests[0].url.host, "oapi.dingtalk.com")
        self.assertEqual(self.selections, original)

    def test_missing_consent_or_stale_fingerprint_blocks_before_http(self):
        self.transport()
        for kw in ({"consent": False}, {"fingerprint": "stale"}, {"fingerprint": ""}):
            with self.subTest(kw=kw), self.assertRaises(DingTalkError):
                self.submit(**kw)
        self.assertEqual(self.requests, [])

    def test_missing_required_people_blocks_before_http(self):
        self.transport()
        with self.assertRaises(DingTalkError): self.submit(selections={})
        self.assertEqual(self.requests, [])

    def test_people_outside_range_or_single_select_blocks(self):
        self.transport()
        for users in ([{"user_id": "outside"}], [{"user_id": "u1"}, {"user_id": "u2"}]):
            selections = deepcopy(self.selections); selections["director"] = users
            with self.subTest(users=users), self.assertRaises(DingTalkError):
                self.submit(selections=selections)
        self.assertEqual(self.requests, [])

    def test_creation_error_never_switches_to_new_api_or_retries(self):
        self.transport(400, {"code": "processInstanceStartFailed", "message": "test error"})
        with self.assertRaises(DingTalkError): self.submit()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, "/topapi/processinstance/create")

    def test_timeout_is_uncertain_and_never_retries(self):
        self.transport(error=httpx.ReadTimeout)
        with self.assertRaises(DingTalkUncertainSubmissionError): self.submit()
        self.assertEqual(len(self.requests), 1)

    def test_success_without_instance_id_is_uncertain(self):
        self.transport(data={"errcode": 0})
        with self.assertRaises(DingTalkUncertainSubmissionError): self.submit()
        self.assertEqual(len(self.requests), 1)

    def test_fixed_only_keeps_exact_legacy_contract(self):
        self.transport(data={"process_instance_id": "test-fixed"})
        fields = [{"name": "department", "value": "456,789", "componentType": "DepartmentField"}]
        self.assertEqual(self.client.start_process(fields, self.fixed, {}, "local-fixed"), "test-fixed")
        req = self.requests[0]; payload = json.loads(req.content)
        self.assertEqual(str(req.url), "https://oapi.dingtalk.com/topapi/processinstance/create?access_token=test-token")
        self.assertEqual(set(payload), {"agent_id", "process_code", "originator_user_id", "dept_id",
                                       "request_id", "form_component_values"})
        self.assertEqual(json.loads(payload["form_component_values"][0]["value"]), [456, 789])

    def test_public_config_describes_explicit_chain_not_actor_key_binding(self):
        config = self.client.public_config()
        self.assertEqual(config["approval_submit_api"], "legacy_topapi")
        self.assertFalse(config["manual_node_binding_supported"])
        self.assertTrue(config["legacy_full_chain_supported"])


class LegacyChainTest(unittest.TestCase):
    def setUp(self): self.nodes, self.selections = fixture()
    def chain(self, nodes=None, selections=None):
        return build_legacy_chain(nodes if nodes is not None else self.nodes,
                                  selections if selections is not None else self.selections)

    def test_fixture_has_no_errors_and_explicit_preview(self):
        chain = self.chain()
        self.assertEqual(chain["errors"], [])
        self.assertEqual(chain["mode"], "explicit_chain")
        self.assertEqual(len(chain["nodes"]), 9)
        self.assertTrue(chain["warnings"])

    def test_optional_unselected_node_is_shown_skipped(self):
        self.nodes[2]["required"] = False; del self.selections["director"]
        chain = self.chain()
        self.assertEqual(chain["errors"], [])
        self.assertTrue(chain["nodes"][2]["skipped"])
        self.assertEqual(len(chain["approvers_v2"]), 6)

    def test_serial_and_or_are_not_flattened(self):
        self.nodes[2]["allowed_multi"] = True
        self.selections["director"] = [{"user_id": "u1"}, {"user_id": "u2"}]
        for method, activation, expected in [
            ("ONE_BY_ONE", "ONE_BY_ONE", [{"task_action_type": "NONE", "user_ids": ["u1"]}, {"task_action_type": "NONE", "user_ids": ["u2"]}]),
            ("AND", "ALL", [{"task_action_type": "AND", "user_ids": ["u1", "u2"]}]),
            ("OR", "ALL", [{"task_action_type": "OR", "user_ids": ["u1", "u2"]}])]:
            self.nodes[2].update(approval_method=method, activation_type=activation)
            chain = self.chain()
            with self.subTest(method=method):
                self.assertEqual(chain["errors"], [])
                self.assertEqual(chain["nodes"][2]["legacy_steps"], expected)

    def test_conflicting_activation_is_blocked(self):
        self.nodes[2].update(allowed_multi=True, approval_method="AND", activation_type="ONE_BY_ONE")
        self.selections["director"] = [{"user_id": "u1"}, {"user_id": "u2"}]
        self.assertTrue(self.chain()["errors"])

    def test_unknown_role_auto_approval_and_unparsed_people_are_blocked(self):
        for changes in [{"actor_type": "audit"}, {"approval_type": "AUTO_AGREE"},
                        {"approval_type": "AUTO_REFUSE"}, {"approval_type": ""},
                        {"approval_method": "UNKNOWN"}, {"activation_type": ""},
                        {"actioners_complete": False}, {"fixed_actioners": []},
                        {"fixed_actioners": [{"user_id": ""}]}, {"metadata_version": 1},
                        {"activity_type": "unknown"}]:
            nodes = deepcopy(self.nodes); nodes[5].update(changes)
            with self.subTest(changes=changes): self.assertTrue(self.chain(nodes)["errors"])

    def test_middle_cc_or_unequal_start_finish_cc_is_blocked(self):
        nodes = deepcopy(self.nodes); nodes[5].update(actor_type="notifier")
        self.assertTrue(self.chain(nodes)["errors"])
        self.nodes[-1]["fixed_actioners"] = [{"user_id": "other"}]
        self.assertTrue(self.chain()["errors"])

    def test_cc_boundaries_ignore_skipped_optional_nodes(self):
        nodes = [node("optional", "optional", True, required=False),
                 node("cc", "optional", users=["cc"], role="notifier"),
                 node("fixed", "cc"), node("manual", "fixed", True),
                 node("finish", "manual", users=["cc"], role="notifier"),
                 node("last-optional", "finish", True, required=False)]
        chain = self.chain(nodes, {"manual": [{"user_id": "u1"}]})
        self.assertEqual(chain["errors"], [])
        self.assertEqual(chain["cc_position"], "START_FINISH")

    def test_cc_start_and_finish_individually(self):
        for nodes, position in [(self.nodes[:-1], "START"), (self.nodes[1:], "FINISH")]:
            chain = self.chain(nodes)
            with self.subTest(position=position):
                self.assertEqual(chain["errors"], [])
                self.assertEqual(chain["cc_position"], position)

    def test_duplicate_or_missing_actor_key_blocks(self):
        for key in ["", "director"]:
            nodes = deepcopy(self.nodes); nodes[3]["actor_key"] = key
            with self.subTest(key=key): self.assertTrue(self.chain(nodes)["errors"])

    def test_duplicate_or_delimited_user_ids_block(self):
        for users in [[{"user_id": "u1"}, {"user_id": "u1"}], [{"user_id": "u1,u2"}]]:
            selections = deepcopy(self.selections); selections["director"] = users
            self.assertTrue(self.chain(selections=selections)["errors"])

    def test_legacy_step_person_and_cc_limits(self):
        nodes = [node(str(i), str(i-1) if i else "0", manual=(i == 0)) for i in range(21)]
        self.assertTrue(self.chain(nodes, {"0": [{"user_id": "u1"}]})["errors"])
        nodes = deepcopy(self.nodes); nodes[1]["fixed_actioners"] = [{"user_id": str(i)} for i in range(21)]
        self.assertTrue(self.chain(nodes)["errors"])
        nodes = deepcopy(self.nodes); nodes[0]["fixed_actioners"] = [{"user_id": str(i)} for i in range(21)]
        self.assertTrue(self.chain(nodes)["errors"])

    def test_branches_cycles_disconnected_missing_prev_or_duplicate_ids_block(self):
        for change in [lambda n: n[3].update(prev_activity_id="fixed"),
                       lambda n: n[0].update(prev_activity_id="cc-end"),
                       lambda n: n[4].update(prev_activity_id="missing"),
                       lambda n: n[4].update(prev_activity_id=None),
                       lambda n: n[4].update(activity_id="manager")]:
            nodes = deepcopy(self.nodes); change(nodes)
            self.assertTrue(self.chain(nodes)["errors"])

    def test_path_order_is_topological_not_response_array_order(self):
        ordered, errors = order_forecast_path(list(reversed(self.nodes)))
        self.assertEqual(errors, [])
        self.assertEqual(ordered, self.nodes)
        self.assertEqual(self.chain(list(reversed(self.nodes)))["fingerprint"], self.chain()["fingerprint"])

    def test_signature_changes_when_people_order_mode_role_or_ranges_change(self):
        signature = forecast_signature(self.nodes)
        for changes in [{"fixed_actioners": [{"user_id": "other"}]}, {"approval_method": "OR"},
                        {"activation_type": "ALL"}, {"actor_type": "notifier"},
                        {"prev_activity_id": "other"}, {"selection_range_resolvable": False},
                        {"path_errors": ["unsafe"]}]:
            nodes = deepcopy(self.nodes); nodes[1].update(changes)
            with self.subTest(changes=changes): self.assertNotEqual(signature, forecast_signature(nodes))

    def test_normalization_preserves_metadata_and_does_not_use_work_number(self):
        raw = {"isForecastSuccess": True, "workflowActivityRules": [
            {"activityId": "m", "prevActivityId": "m", "activityName": "manual", "activityType": "target_select",
             "workflowActor": {"actorType": "approver", "approvalType": "MANUAL", "actorKey": "m",
                               "approvalMethod": "AND", "actorActivateType": "ALL", "required": True, "allowedMulti": True}},
            {"activityId": "f", "prevActivityId": "m", "activityType": "target_approval",
             "workflowActor": {"actorType": "approver", "approvalType": "MANUAL", "approvalMethod": "ONE_BY_ONE", "actorActivateType": "ONE_BY_ONE"},
             "activityActioners": [{"workNo": "not-a-user-id", "name": "fixed"}]}]}
        parsed = normalize_forecast(raw)
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["nodes"][0]["approval_method"], "AND")
        self.assertFalse(parsed["nodes"][1]["actioners_complete"])
        self.assertEqual(parsed["nodes"][1]["fixed_actioners"], [])
        self.assertTrue(build_legacy_chain(parsed["nodes"], {"m": [{"user_id": "u1"}]})["errors"])

    def test_malformed_rule_or_actor_fails_normalization(self):
        for rules in [["bad"], "bad", [{"workflowActor": ["bad"]}]]:
            parsed = normalize_forecast({"isForecastSuccess": True, "workflowActivityRules": rules})
            with self.subTest(rules=rules):
                self.assertFalse(parsed["success"])
                self.assertTrue(parsed["path_errors"])


class PreviewQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {"APP_DATA_DIR": self.temp.name}):
            self.main = importlib.import_module("app.main")
        self.nodes, self.selections = fixture()
        self.profile = {"payment_entity": "company", "supplier_name": "supplier", "bank_account": "test", "bank_name": "bank"}
        self.group = {"id": "g1", "company": "test-company", "payment_department": "department", "count": 1,
                      "amount": 100, "file_name": "test.xlsx", "file_path": "test.xlsx"}
        self.task = {"id": "test-task", "result": {"month": "9月", "departments": [self.group]},
                     "workflow_forecasts": {"g1": {"nodes": self.nodes, "signature": forecast_signature(self.nodes)}},
                     "workflow_actioners": {"g1": self.selections}}
        config = DingTalkConfig(agent_id="123", process_code="TEST", originator_user_id="u", originator_dept_id="456", live_submit_enabled=True)
        for obj, name, kw in [(self.main.db, "get_profile", {"return_value": self.profile}),
                              (self.main.dingtalk, "config", {"new": config}),
                              (self.main.db, "get_attempt", {"return_value": None}),
                              (self.main.db, "save_task", {}), (self.main.db, "save_attempt", {})]:
            patcher = patch.object(obj, name, **kw); value = patcher.start(); self.addCleanup(patcher.stop)
            if name == "save_attempt": self.save_attempt = value
            if name == "get_attempt": self.get_attempt = value
        self.task["previews"], valid = self.main.preview_items(self.task)
        self.assertTrue(valid)

    def confirmation(self, **kw):
        return self.main.ConfirmSubmit(confirm=kw.get("confirm", True), confirm_legacy_chain=kw.get("consent", True),
            preview_fingerprints=kw.get("fingerprints", {i["group_id"]: i["confirmation_fingerprint"] for i in self.task["previews"]}))

    def test_valid_full_chain_preview_and_queue_preserve_people(self):
        original = deepcopy(self.selections)
        pending, total = self.main.queue_submission(self.task, False, self.confirmation())
        self.assertEqual(total, 1); self.assertEqual(len(pending), 1)
        self.assertEqual(self.save_attempt.call_count, 1)
        self.assertEqual(pending[0][2]["payload"]["submission_mode"], "explicit_chain")
        self.assertEqual(pending[0][2]["payload"]["confirmed_chain_fingerprint"], pending[0][0]["legacy_chain"]["fingerprint"])
        self.assertEqual(original, self.selections)

    def test_no_consent_stale_preview_or_missing_confirmation_never_queues(self):
        for kw in [{"consent": False}, {"confirm": False}, {"fingerprints": {"g1": "stale"}}, {"fingerprints": {}}]:
            with self.subTest(kw=kw), self.assertRaises(HTTPException):
                self.main.queue_submission(self.task, False, self.confirmation(**kw))
        self.save_attempt.assert_not_called()

    def test_changed_selection_profile_amount_and_fixed_people_invalidate_preview(self):
        for change in [lambda: self.task["workflow_actioners"]["g1"]["director"].append({"user_id": "u2"}),
                       lambda: self.profile.update(bank_account="changed"),
                       lambda: self.group.update(amount=200),
                       lambda: self.nodes[1].update(fixed_actioners=[{"user_id": "other"}])]:
            old_task, old_profile = deepcopy(self.task), deepcopy(self.profile)
            change()
            with self.assertRaises(HTTPException): self.main.queue_submission(self.task, False, self.confirmation())
            self.task = old_task; self.profile.clear(); self.profile.update(old_profile)
            self.group = self.task["result"]["departments"][0]; self.nodes = self.task["workflow_forecasts"]["g1"]["nodes"]
        self.save_attempt.assert_not_called()

    def test_only_failed_candidates_are_validated_success_and_uncertain_skipped(self):
        for status in ["submitted", "uncertain"]:
            group2 = {**self.group, "id": "g2"}
            self.task["result"]["departments"] = [self.group, group2]
            # g2 intentionally has no forecast or preview; must not block g1 retry.
            self.get_attempt.side_effect = lambda _, key: {"status": "failed", "request_id": "r1"} if key == "g1" else {"status": status}
            pending, total = self.main.queue_submission(self.task, True, self.confirmation())
            with self.subTest(status=status):
                self.assertEqual(total, 2)
                self.assertEqual([x[0]["group_id"] for x in pending], ["g1"])
                self.assertEqual(pending[0][2]["request_id"], "r1")

    def test_all_candidates_validated_before_first_write(self):
        group2 = {**self.group, "id": "g2"}; self.task["result"]["departments"].append(group2)
        with self.assertRaises(HTTPException): self.main.queue_submission(self.task, False, self.confirmation())
        self.save_attempt.assert_not_called()

    def test_resolved_fields_from_old_profile_require_new_forecast(self):
        self.task["workflow_forecasts"]["g1"]["resolved_fields"] = [{"name": "old", "value": "old"}]
        _, valid = self.main.preview_items(self.task)
        self.assertFalse(valid)


class EditGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_edit_routes_reject_while_submission_lock_is_held(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"APP_DATA_DIR": temp}):
            main = importlib.import_module("app.main")
        lock = asyncio.Lock(); main.submission_locks["locked-test"] = lock
        await lock.acquire()
        try:
            for handler, args in [(main.update_groups, [None]), (main.prepare_task, []),
                                  (main.approval_forecast, []), (main.update_workflow_actioners, [None]),
                                  (main.approval_preview, [])]:
                with self.subTest(handler=handler.__name__), self.assertRaises(HTTPException) as caught:
                    await handler("locked-test", *args)
                self.assertEqual(caught.exception.status_code, 409)
        finally:
            lock.release(); main.submission_locks.pop("locked-test", None)

    async def test_edit_guard_releases_lock_after_exception(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"APP_DATA_DIR": temp}):
            main = importlib.import_module("app.main")
        @main.exclusive_task_edit
        async def bad(task_id): raise ValueError("mock")
        with self.assertRaises(ValueError): await bad("edit-exception")
        self.assertFalse(main.submission_locks.pop("edit-exception").locked())


if __name__ == "__main__": unittest.main()
