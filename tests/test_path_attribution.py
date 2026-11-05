from __future__ import annotations
import unittest
from copy import deepcopy
from app.legacy_chain import CHAIN_VERSION, inspect_forecast_path, build_legacy_chain
from app.approval_cc import compatibility
from app.dingtalk import normalize_forecast
from test_manual_submission import node


class PathAttributionTest(unittest.TestCase):
    def setUp(self):
        self.nodes = [node("manager", "opaque-last"), node("department", "manager"),
                      node("accountant", "department"), node("finance", "gateway"),
                      node("general", "finance"), node("cashier", "general", actor_type="audit"),
                      node("cc", "cashier", manual=True, role="notifier", required=False, allowed_multi=True)]
        self.selections = {"cc": [{"user_id": "u1", "name": "mock-cc"}]}
        self.graph = [{"activityId": "start", "outId": "line-random-start-gateway"},
                      {"activityId": "gateway", "outId": "opaque-first"},
                      {"activityId": "route", "outId": "opaque-last"}]
        for left, right in zip(self.nodes, self.nodes[1:]):
            self.graph.append({"activityId": left["activity_id"], "outId": "line-random-" + left["activity_id"] + "-" + right["activity_id"]})
        self.graph.extend([{"activityId": "cc", "outId": "end"}, {"activityId": "end"}])
        for n in self.nodes: n["forecast_path"] = self.graph

    def test_opaque_routes_record_all_gaps_without_losing_confirmed_business_edges(self):
        _, errors, diagnostic = inspect_forecast_path(self.nodes)
        self.assertEqual(len(errors), 1)
        self.assertIn("2 处", errors[0])
        self.assertIn("不代表抄送人选择错误", errors[0])
        self.assertEqual(diagnostic["business_node_count"], 7)
        self.assertEqual(len(diagnostic["business_connections"]), 6)
        self.assertEqual([edge["out_id"] for edge in diagnostic["unresolved_connections"]], ["opaque-first", "opaque-last"])
        self.assertFalse(diagnostic["order_confirmed"])

    def test_array_and_prev_ids_are_not_used_to_invent_opaque_connections(self):
        for graph in [self.graph, list(reversed(self.graph))]:
            _, errors, diagnostic = inspect_forecast_path(list(reversed(self.nodes)), graph)
            self.assertTrue(errors)
            self.assertFalse(diagnostic["order_confirmed"])
            self.assertEqual(len(diagnostic["unresolved_connections"]), 2)

    def test_full_route_only_confirmed_after_explicit_targets_are_present(self):
        graph = deepcopy(self.graph);graph[1]["outId"] = "route";graph[2]["outId"] = "manager"
        ordered, errors, diagnostic = inspect_forecast_path(list(reversed(self.nodes)), graph)
        self.assertFalse(errors)
        self.assertEqual([n["activity_id"] for n in ordered], [n["activity_id"] for n in self.nodes])
        self.assertTrue(diagnostic["order_confirmed"])
        for n in self.nodes:n["forecast_path"] = graph
        checked = compatibility(self.nodes, self.selections)
        self.assertEqual(checked["status"], "incompatible")
        self.assertTrue(any(i["code"] == "template_manual_binding_unverified" for i in checked["issues"]))
        self.assertTrue(checked["global_issues"])
        self.assertEqual(checked["chain"]["mode"], "template")

    def test_workflow_blockers_do_not_mark_cc_as_incompatible(self):
        checked = compatibility(self.nodes, self.selections)
        self.assertEqual(checked["status"], "incompatible")
        cc = self.nodes[-1]["compatibility"]
        self.assertEqual(cc["status"], "verified")
        self.assertEqual(cc["issues"], [])
        self.assertTrue(cc["workflow_blocked"])
        audit = self.nodes[-2]["compatibility"]
        self.assertEqual(audit["status"], "verified")
        self.assertEqual(audit["execution_owner"], "dingtalk")
        self.assertEqual(audit["issues"], [])
        self.assertTrue(checked["global_issues"])
        self.assertTrue(build_legacy_chain(self.nodes, self.selections)["errors"])

    def test_actual_cc_error_still_appears_on_cc_node(self):
        checked = compatibility(self.nodes, {"cc": [{"user_id": "unauthorized", "name": "not-allowed"}]})
        self.assertEqual(checked["status"], "incompatible")
        self.assertEqual(self.nodes[-1]["compatibility"]["status"], "incompatible")
        self.assertTrue(any(i["activity_id"] == "cc" for i in self.nodes[-1]["compatibility"]["issues"]))

    def test_global_cc_limit_is_not_attributed_to_cc_name_prefix(self):
        nodes = [node("approve", "approve"), node("抄送人", "approve", role="notifier", users=[str(i) for i in range(12)]),
                 node("通知", "抄送人", role="notifier", users=[str(i) for i in range(12, 22)])]
        checked = compatibility(nodes, {})
        self.assertTrue(checked["global_issues"])
        self.assertEqual(nodes[-1]["compatibility"]["issues"], [])
        self.assertEqual(checked["status"], "incompatible")

    def test_malformed_and_parallel_paths_remain_blocked(self):
        for graph in [[], [{"activityId": "manager", "outId": ["a", "b"]}], [None]]:
            self.assertTrue(inspect_forecast_path(self.nodes, graph)[1])
        graph = deepcopy(self.graph);graph[1]["outId"] = "route";graph[2]["outId"] = "manager";graph[-1]["outId"] = "manager"
        self.assertTrue(inspect_forecast_path(self.nodes, graph)[1])

    def test_new_metadata_and_normalized_diagnostics_require_old_forecast_refresh(self):
        self.assertGreater(CHAIN_VERSION, 3)
        old = node("approve", "approve", metadata_version=3)
        self.assertTrue(build_legacy_chain([old], {})["errors"])
        raw = {"isForecastSuccess": True, "workflowForecastNodes": [{"activityId": "a", "outId": "opaque"}],
               "workflowActivityRules": [{"activityId": "a", "activityName": "a", "activityType": "target_label",
                   "workflowActor": {"actorType": "approver", "approvalType": "MANUAL", "approvalMethod": "OR", "actorActivateType": "ALL"},
                   "activityActioners": [{"userId": "mock"}]}]}
        normalized = normalize_forecast(raw)
        self.assertEqual(normalized["metadata_version"], CHAIN_VERSION)
        self.assertEqual(normalized["path_diagnostics"]["unresolved_connections"][0]["source_kind"], "business")
        self.assertTrue(normalized["path_errors"])

if __name__ == "__main__": unittest.main()
