import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
import httpx
from app.dingtalk import DingTalkClient, DingTalkConfig, DingTalkError, wire_form_values, normalize_forecast
from app.modern_diagnostics import ReadOnlyTransport, READ_ONLY_ROUTES, audit_request
from app.modern_chain import build_modern_template_chain


def schema():
    return {"formCode": "TEST", "schemaContent": json.dumps({"items": [
        {"componentName": "TextField", "props": {"id": "x", "label": "事由", "required": True}},
        {"componentName": "TableField", "props": {"id": "t", "label": "明细"}, "children": [
            {"componentName": "NumberField", "props": {"id": "n", "label": "金额", "required": True}}]}]})}

def request():
    return {"processCode": "TEST", "originatorUserId": "fake-user", "deptId": 2, "microappAgentId": 3,
            "formComponentValues": [{"name": "事由", "value": "模拟接口，不付款"},
                {"name": "明细", "value": json.dumps([[{"name": "金额", "value": "0"}]])}],
            "targetSelectActioners": [{"actionerKey": "manual_a", "actionerUserIds": ["fake-a"]}]}

class DiagnosticTests(unittest.TestCase):
    def codes(self, payload=None, form=None, **kw):
        return {f["code"] for f in audit_request(form or schema(), payload or request(), **kw)["findings"]}

    def test_offline_report_never_contacts_network_or_exposes_users(self):
        with patch.object(httpx.Client, "request", side_effect=AssertionError("network")):
            result = audit_request(schema(), request())
        self.assertEqual(result["structural_errors"], 0)
        self.assertEqual(result["real_create_calls"], 0)
        self.assertIn("SERVER_ACCEPTANCE", {f["code"] for f in result["findings"]})
        self.assertNotIn("fake-user", json.dumps(result))
        self.assertNotIn("fake-a", json.dumps(result))

    def test_binding_keys_must_match_forecast_not_node_names(self):
        f = {"success": True, "nodes": [{"name": "manager", "kind": "manual", "actor_key": "manual_a", "required": True}]}
        self.assertNotIn("BINDING_NOT_IN_FORECAST", self.codes(forecast=f))
        p = request(); p["targetSelectActioners"][0]["actionerKey"] = "manager"
        self.assertIn("BINDING_NOT_IN_FORECAST", self.codes(p, forecast=f))
        self.assertIn("REQUIRED_BINDING_MISSING", self.codes(p, forecast=f))

    def test_template_identity_and_no_chain_override(self):
        p = request();p["processCode"] = "OTHER";p["approvers"] = [];p["requestId"] = "local"
        self.assertTrue({"PROCESS_MISMATCH", "TEMPLATE_OVERRIDE", "UNDECLARED_REQUEST_ID"} <= self.codes(p))

    def test_duplicate_and_invalid_binding_users(self):
        for users, code in [(["fake-a", "fake-a"], "BINDING_DUPLICATE_USER"), ([None], "BINDING_USERS"), ([], "BINDING_USERS")]:
            p = request();p["targetSelectActioners"][0]["actionerUserIds"] = users
            self.assertIn(code, self.codes(p))
        p = request();p["targetSelectActioners"] *= 2
        self.assertIn("BINDING_DUPLICATE_KEY", self.codes(p))

    def test_bad_field_and_table_shapes(self):
        p = request();p["formComponentValues"][0]["value"] = 0
        self.assertIn("FORM_VALUE_TYPE", self.codes(p))
        p = request();p["formComponentValues"][1]["value"] = "[[1]]"
        self.assertIn("TABLE_ROW", self.codes(p))
        p["formComponentValues"][1]["value"] = json.dumps([[{"name":"金额", "value":"NaN"}]])
        self.assertIn("TABLE_NUMBER", self.codes(p))
        p["formComponentValues"][1]["value"] = "[[]]"
        self.assertIn("TABLE_REQUIRED", self.codes(p))

    def test_conditional_required_and_calculation_are_unverified_not_assumed(self):
        s = schema();content = json.loads(s["schemaContent"])
        content["items"] += [{"componentName":"DDSelectField", "props":{"label":"条件", "behaviorLinkage":[{"targets":[{"fieldId":"hidden"}]}]}},
            {"componentName":"TextField", "props":{"id":"hidden", "label":"条件必填", "required":True}},
            {"componentName":"CalculateField", "props":{"label":"总额", "required":True}}]
        s["schemaContent"] = json.dumps(content)
        result = audit_request(s, request())
        codes = {f["code"] for f in result["findings"]}
        self.assertTrue({"CONDITIONAL_REQUIRED", "CALCULATED_OMITTED"} <= codes)
        self.assertEqual(result["structural_errors"], 0)
    def test_table_details_missing_valid_and_inconsistent(self):
        p = request()
        self.assertIn("TABLE_DETAILS_OMITTED", self.codes(p))
        p["formComponentValues"][1]["details"] = [{"name":"金额", "value":"0"}]
        self.assertNotIn("TABLE_DETAILS_OMITTED", self.codes(p))
        self.assertEqual(audit_request(schema(), p)["structural_errors"], 0)
        p["formComponentValues"][1]["details"][0]["value"] = "0.01"
        self.assertIn("TABLE_DETAILS_MISMATCH", self.codes(p))
        p["formComponentValues"][1]["details"] = [[{"name":"金额", "value":"0"}]]
        self.assertIn("TABLE_DETAILS_SHAPE", self.codes(p))

    def test_old_department_scalar_only_flagged_for_review(self):
        s = schema();content=json.loads(s["schemaContent"])
        content["items"].append({"componentName":"DepartmentField", "props":{"label":"部门", "required":True}})
        s["schemaContent"]=json.dumps(content);p=request();p["formComponentValues"].append({"name":"部门", "value":"123"})
        self.assertIn("DEPARTMENT_SCALAR", self.codes(p, s))
        self.assertEqual(audit_request(s,p)["structural_errors"],0)

    def test_unknown_routing_metadata_does_not_rebuild_template_chain(self):
        nodes=[{"name":"现金办理", "kind":"fixed", "actor_type":"audit", "fixed_actioners":[{"user_id":"cash"}], "path_errors":["opaque route"]},
               {"name":"抄送", "kind":"manual", "actor_type":"notifier", "actor_key":"manual_cc"}]
        chain=build_modern_template_chain(nodes,{"manual_cc":[{"user_id":"cc"}]})
        self.assertEqual(chain["errors"],[])
        self.assertEqual([n["role"] for n in chain["nodes"]],["audit","notifier"])
        self.assertEqual(chain["approvers_v2"],[])
        self.assertTrue(chain["fingerprint"])

    def test_whitelist_blocks_create_upload_completion_fallback_and_wrong_hosts(self):
        calls=[]
        inner=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200,json={"ok":True}))
        guard=ReadOnlyTransport(inner)
        with httpx.Client(transport=guard,follow_redirects=False) as c:
            for url in ["https://api.dingtalk.com/v1.0/workflow/processInstances", "https://api.dingtalk.com/v1.0/storage/uploadInfos/query",
                        "https://oapi.dingtalk.com/topapi/processinstance/create", "https://api.dingtalk.com/v1.0/workflow/processInstances/execute",
                        "https://evil.invalid/v1.0/workflow/processes/forecast", "http://api.dingtalk.com/v1.0/workflow/processes/forecast",
                        "https://api.dingtalk.com:444/v1.0/workflow/processes/forecast"]:
                with self.assertRaises(RuntimeError): c.post(url)
            self.assertEqual(len(calls),0)
            for method,path in READ_ONLY_ROUTES:c.request(method,"https://api.dingtalk.com"+path)
        self.assertEqual(len(calls),3)
        self.assertEqual(guard.blocked,7)
        self.assertNotIn("headers", json.dumps(guard.calls))

    def test_whitelist_redirect_is_not_followed(self):
        calls=[]
        def inner(r):
            calls.append(r)
            return httpx.Response(307,headers={"location":"https://api.dingtalk.com/v1.0/workflow/processInstances"})
        guard=ReadOnlyTransport(httpx.MockTransport(inner))
        with httpx.Client(transport=guard,follow_redirects=False) as c:
            self.assertEqual(c.post("https://api.dingtalk.com/v1.0/workflow/processes/forecast").status_code,307)
        self.assertEqual(len(calls),1)

    def test_wire_keeps_zero(self):
        self.assertEqual(wire_form_values([{"name":"数字", "value":0}]), [{"name":"数字", "value":"0"}])

class PreparedPayloadTests(unittest.TestCase):
    def setUp(self):
        self.client = DingTalkClient(DingTalkConfig(originator_user_id="fake", originator_dept_id="2", agent_id="3", process_code="TEST"))
        self.addCleanup(self.client.close)
        self.client._request=Mock(side_effect=AssertionError("network forbidden"))
        self.manual={"activity_id":"a", "name":"选择人", "kind":"manual", "actor_key":"manual_a", "required":True,
                     "allowed_multi":True, "selection_range_restricted":True, "selection_range_resolvable":True, "allowed_user_ids":["a","b"]}
        self.selections={"manual_a":[{"user_id":"a"}]}
        self.fields=[{"name":"事由", "value":"不付款"}]

    def test_payload_preparation_works_with_submission_disabled_without_request(self):
        p=self.client.prepare_start_payload(self.fields,[self.manual],self.selections)
        self.assertEqual(p["targetSelectActioners"],[{"actionerKey":"manual_a", "actionerUserIds":["a"]}])
        self.assertFalse(self.client.config.live_submit_enabled)
        self.client._request.assert_not_called()

    def test_invalid_duplicate_missing_and_stale_selections_never_send(self):
        for selection in [[{"user_id":"a"},{"user_id":"a"}],[{}],[None],"a"]:
            with self.assertRaises(DingTalkError):
                self.client.prepare_start_payload(self.fields,[self.manual],{"manual_a":selection})
        with self.assertRaises(DingTalkError):
            self.client.prepare_start_payload(self.fields,[self.manual],dict(self.selections,old_node=[{"user_id":"a"}]))
        self.client._request.assert_not_called()

    def test_full_template_with_audit_and_manual_cc_does_not_override_other_nodes(self):
        cc=dict(self.manual,actor_key="manual_cc",actor_type="notifier",required=False)
        audit={"activity_id":"cash", "kind":"fixed", "name":"出纳", "actor_type":"audit", "fixed_actioners":[{"user_id":"cash"}]}
        nodes=[self.manual,audit,cc];original=deepcopy(nodes)
        selections=dict(self.selections,manual_cc=[{"user_id":"b"}])
        p=self.client.prepare_start_payload(self.fields,nodes,selections)
        self.assertEqual(len(p["targetSelectActioners"]),2)
        self.assertEqual(nodes,original)
        for key in ("approvers","approvers_v2","ccList","ccPosition"):self.assertNotIn(key,p)
        self.client._request.assert_not_called()

class MockTemplateWireTests(unittest.TestCase):
    setUp = PreparedPayloadTests.setUp
    def test_mock_http_retains_handler_and_binds_both_manual_roles(self):
        from dataclasses import replace
        cc = dict(self.manual, actor_key="manual_cc", actor_type="notifier", required=False)
        handler = {"activity_id":"cash", "kind":"fixed", "name":"出纳", "actor_type":"audit",
                   "fixed_actioners":[{"user_id":"fake-cashier"}]}
        nodes = [self.manual, handler, cc]
        selections = dict(self.selections, manual_cc=[{"user_id":"b"}])
        calls=[]
        def respond(r):
            calls.append(r)
            self.assertEqual(r.url.path,"/v1.0/workflow/processInstances")
            body=json.loads(r.content)
            self.assertEqual(body["targetSelectActioners"],[{"actionerKey":"manual_a","actionerUserIds":["a"]},
                                                          {"actionerKey":"manual_cc","actionerUserIds":["b"]}])
            for k in ("approvers","approvers_v2","ccList","ccPosition"):self.assertNotIn(k,body)
            return httpx.Response(200,json={"instanceId":"mock-only-no-real-approval"})
        self.client.close();self.client._http=httpx.Client(transport=httpx.MockTransport(respond))
        self.client.access_token=Mock(return_value="mock-token")
        self.client._request=DingTalkClient._request.__get__(self.client)
        self.client.config=replace(self.client.config,live_submit_enabled=True)
        self.assertEqual(self.client.start_process(self.fields,nodes,selections,"local"),"mock-only-no-real-approval")
        self.assertEqual(len(calls),1)
        self.assertEqual(nodes[1]["actor_type"],"audit")

    def test_readonly_command_preserves_archives_even_when_config_flag_true(self):
        import importlib.util,tempfile
        from pathlib import Path
        from dataclasses import replace
        spec=importlib.util.spec_from_file_location("readonly_script",Path("scripts/diagnose_modern_readonly.py"))
        script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in script.PROTECTED:
                value={}
                if name=="modern_schema.json":value=schema()
                if name=="modern_create_request.json":value=request()
                if name=="modern_live_report.json":value={"nodes":[{"kind":"manual","actor_key":"manual_a"}],"create_calls":1}
                (root/name).write_text(json.dumps(value),encoding="utf-8")
            with patch.object(script,"OUT",root), patch.object(script,"load_dingtalk_config",return_value=replace(self.client.config,live_submit_enabled=True)), \
                 patch.object(httpx.Client,"request",side_effect=AssertionError("network")), \
                 patch.object(DingTalkClient,"start_process",side_effect=AssertionError("create forbidden")), \
                 patch.object(DingTalkClient,"upload_approval_attachment",side_effect=AssertionError("upload forbidden")):
                before=script.digest();result=script.run(False)
                self.assertEqual(script.digest(),before)
                self.assertTrue(result["original_evidence_unchanged"])
                self.assertEqual(result["real_create_calls"],0)
                self.assertEqual(result["network_calls"],[])

if __name__=="__main__":unittest.main()
