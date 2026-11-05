from __future__ import annotations

import json
import unittest
from copy import deepcopy

import httpx
from app.approval_cc import compatibility
from app.dingtalk import DingTalkClient, DingTalkConfig, DingTalkError, normalize_forecast
from app.legacy_chain import CHAIN_VERSION, build_legacy_chain
from test_manual_submission import node


def handler_path(manual=False):
    nodes = [node("approval", "approval"),
             node("cashier", "approval", role="audit", activity_type="target_label",
                  approval_type="", approval_method="", activation_type=""),
             node("cc", "cashier", role="notifier")]
    selections = {}
    if manual:
        nodes.insert(1, node("department", "approval", manual=True))
        nodes[2]["prev_activity_id"] = "department"
        selections = {"department": [{"user_id": "u1", "name": "selected-user"}]}
    return nodes, selections


class TemplateHandlerTest(unittest.TestCase):
    """No live HTTP: every submission is handled by httpx.MockTransport."""
    def setUp(self):
        self.nodes, self.selections = handler_path()
        self.requests = []
        self.client = DingTalkClient(DingTalkConfig(agent_id="123", process_code="TEST-TEMPLATE",
            originator_user_id="test-user", originator_dept_id="456", live_submit_enabled=True))
        self.client._http.close()
        def handle(request):
            self.requests.append(request)
            return httpx.Response(200, json={"errcode": 0, "process_instance_id": "mock-only-instance"})
        self.client._http = httpx.Client(transport=httpx.MockTransport(handle))
        self.client.access_token = lambda: "mock-only-token"
        self.addCleanup(self.client.close)

    def test_fixed_handler_is_kept_template_owned_never_converted_or_skipped(self):
        original = deepcopy(self.nodes)
        plan = build_legacy_chain(self.nodes, {})
        self.assertFalse(plan["errors"])
        self.assertEqual(plan["mode"], "template")
        self.assertTrue(plan["preserves_template"])
        self.assertEqual(plan["handlers"], ["cashier"])
        self.assertEqual(plan["approvers_v2"], [])
        self.assertEqual(plan["cc_list"], "")
        self.assertTrue(all(not n["skipped"] and not n["legacy_steps"] for n in plan["nodes"]))
        self.assertEqual(plan["nodes"][1]["role"], "audit")
        self.assertEqual(self.nodes, original)

    def test_mock_create_preserves_process_code_without_approver_cc_overrides_or_handler_operations(self):
        instance = self.client.start_process([], self.nodes, {}, "mock-request")
        self.assertEqual(instance, "mock-only-instance")
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(req.url.host, "oapi.dingtalk.com")
        self.assertEqual(req.url.path, "/topapi/processinstance/create")
        payload = json.loads(req.content)
        self.assertEqual(payload["process_code"], "TEST-TEMPLATE")
        self.assertEqual(set(payload), {"agent_id", "process_code", "originator_user_id",
                                       "dept_id", "request_id", "form_component_values"})

    def test_selected_manual_node_blocks_instead_of_rewriting_or_omitting_selection(self):
        nodes, selections = handler_path(manual=True)
        before = deepcopy(selections)
        checked = compatibility(nodes, selections)
        self.assertTrue(checked["chain"]["manual_binding_required"])
        self.assertFalse(checked["chain"]["manual_binding_supported"])
        self.assertEqual(checked["chain"]["approvers_v2"], [])
        self.assertTrue(any(i["code"] == "template_manual_binding_unverified" for i in checked["global_issues"]))
        self.assertEqual(checked["chain"]["nodes"][1]["users"], selections["department"])
        self.assertEqual(selections, before)
        with self.assertRaisesRegex(DingTalkError, "人员绑定"):
            self.client.start_process([], nodes, selections, "mock-block", legacy_chain_confirmed=True,
                                      expected_chain_fingerprint=checked["chain"]["fingerprint"])
        self.assertFalse(self.requests)

    def test_optional_manual_is_not_silently_dropped_even_if_unselected(self):
        nodes, _ = handler_path(manual=True)
        nodes[1]["required"] = False
        with self.assertRaisesRegex(DingTalkError, "人员绑定"):
            self.client.start_process([], nodes, {}, "mock-block")
        self.assertFalse(self.requests)
        self.assertFalse(build_legacy_chain(nodes, {})["nodes"][1]["skipped"])

    def test_manual_cc_defaults_cannot_be_sent_as_a_template_override(self):
        self.nodes[-1].update(kind="manual", actor_key="cc", required=False, allowed_multi=True)
        with self.assertRaisesRegex(DingTalkError, "人员绑定"):
            self.client.start_process([], self.nodes, {"cc": [{"user_id": "u1"}]}, "mock-cc")
        self.assertFalse(self.requests)

    def test_bad_path_remains_blocked_even_for_template_execution(self):
        for n in self.nodes:
            n["forecast_path"] = [{"activityId": "approval", "outId": "opaque"},
                                  {"activityId": "cashier", "outId": "cc"}, {"activityId": "cc"}]
        with self.assertRaises(DingTalkError):
            self.client.start_process([], self.nodes, {}, "mock-gap")
        self.assertFalse(self.requests)
        checked = compatibility(self.nodes, {})
        self.assertEqual(checked["status"], "incompatible")
        self.assertEqual(self.nodes[1]["compatibility"]["issues"], [])
        self.assertEqual(self.nodes[1]["compatibility"]["execution_owner"], "dingtalk")

    def test_handler_identity_missing_still_blocks_its_own_node(self):
        self.nodes[1]["fixed_actioners"] = []
        checked = compatibility(self.nodes, {})
        self.assertEqual(self.nodes[1]["compatibility"]["status"], "incompatible")
        self.assertTrue(any(i["activity_id"] == "cashier" for i in checked["issues"]))
        with self.assertRaises(DingTalkError):
            self.client.start_process([], self.nodes, {}, "mock-missing")
        self.assertFalse(self.requests)

    def test_unknown_role_or_incomplete_metadata_cannot_hide_behind_template_mode(self):
        for update in [{"actor_type": "unknown"}, {"activity_type": "unknown"},
                       {"metadata_version": CHAIN_VERSION - 1}, {"actioners_complete": False}]:
            nodes = deepcopy(self.nodes)
            nodes[0].update(update)
            with self.subTest(update=update), self.assertRaises(DingTalkError):
                self.client.start_process([], nodes, {}, "mock-unknown")
        self.assertFalse(self.requests)

    def test_explicitly_returned_auto_behavior_stays_template_owned_without_human_substitution(self):
        self.nodes[0].update(approval_type="AUTO_AGREE", fixed_actioners=[], actioners_complete=True)
        plan = build_legacy_chain(self.nodes, {})
        self.assertFalse(plan["errors"])
        self.assertEqual(plan["approvers_v2"], [])
        self.assertEqual(plan["nodes"][0]["users"], [])
        self.client.start_process([], self.nodes, {}, "mock-auto")
        self.assertNotIn("approvers_v2", json.loads(self.requests[0].content))

    def test_stale_template_plan_fingerprint_blocks_before_mock_http(self):
        with self.assertRaisesRegex(DingTalkError, "模板执行计划"):
            self.client.start_process([], self.nodes, {}, "mock-stale", expected_chain_fingerprint="stale")
        self.assertFalse(self.requests)

    def test_current_version_invalidation_and_selection_changes_affect_fingerprint(self):
        self.assertGreater(CHAIN_VERSION, 4)
        nodes, selections = handler_path(manual=True)
        plan = build_legacy_chain(nodes, selections)
        selections["department"] = [{"user_id": "u2", "name": "changed"}]
        self.assertNotEqual(plan["fingerprint"], build_legacy_chain(nodes, selections)["fingerprint"])
        nodes[0]["metadata_version"] = 4
        self.assertTrue(build_legacy_chain(nodes, selections)["errors"])

    def test_backend_never_reports_handler_delegation_as_business_completion(self):
        checked = compatibility(self.nodes, {})
        info = self.nodes[1]["compatibility"]
        self.assertEqual(info["execution_owner"], "dingtalk")
        self.assertTrue(info["preserves_template"])
        self.assertNotIn("completed", info)
        self.assertNotIn("submitted", info)
        self.assertEqual(checked["chain"]["nodes"][1]["role"], "audit")


if __name__ == "__main__":
    unittest.main()
