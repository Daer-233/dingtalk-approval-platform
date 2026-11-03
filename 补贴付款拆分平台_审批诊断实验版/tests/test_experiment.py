import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import httpx
from fastapi.testclient import TestClient
from app.dingtalk import DingTalkConfig, DingTalkError, forecast_signature
from app.experiment import DiagnosticDingTalkClient, DiagnosticTransport, READ_ROUTES
from app.experiment_diagnostics import build_report, failure_record, historical_replay, inspect_people, redact
from app.db import Database

ROOT = Path(__file__).resolve().parents[1]

def form():
    return {"formCode": "TEST", "schemaContent": json.dumps({"items": [
        {"componentName": "TextField", "props": {"label": "事由", "required": True}},
        {"componentName": "CalculateField", "props": {"label": "总额", "required": True}},
        {"componentName": "DDAttachment", "props": {"label": "附件", "required": True}},
        {"componentName": "TableField", "props": {"label": "明细"}, "children": [
            {"componentName": "NumberField", "props": {"label": "分摊金额", "required": True}}]}]})}

def payload():
    return {"processCode": "TEST", "originatorUserId": "user", "deptId": 2, "microappAgentId": 3,
            "formComponentValues": [{"name": "事由", "value": "模拟诊断"},
            {"name": "明细", "value": '[[{"name":"分摊金额","value":"100"}]]', "details": [{"name": "分摊金额", "value": "100"}]},
            {"name": "附件", "value": ""}]}

def cfg(): return DingTalkConfig(client_id="test", client_secret="fake", agent_id="3", corp_id="corp",
                               originator_user_id="user", originator_dept_id="2", process_code="TEST",
                               live_submit_enabled=True, attachment_enabled=True)

class ReadOnlyBoundaryTests(unittest.TestCase):
    def test_allow_all_explicit_reads_and_block_all_other_methods_before_network(self):
        received=[]
        inner=httpx.MockTransport(lambda req: (received.append(req), httpx.Response(200, json={}))[1])
        with httpx.Client(transport=DiagnosticTransport(inner)) as client:
            for host, routes in READ_ROUTES.items():
                for method,path in routes: client.request(method, "https://"+host+path)
            accepted=len(received)
            writes=[("POST","https://api.dingtalk.com/v1.0/workflow/processInstances"),
                    ("POST","https://api.dingtalk.com/v1.0/storage/spaces/1/files/uploadInfos/query"),
                    ("POST","https://api.dingtalk.com/v1.0/storage/spaces/1/files/commit"),
                    ("PUT","https://bucket.example.com/upload"),
                    ("POST","https://oapi.dingtalk.com/topapi/processinstance/create"),
                    ("POST","https://oapi.dingtalk.com/topapi/processinstance/execute"),
                    ("POST","https://api.dingtalk.com/v1.0/workflow/processInstances/terminate"),
                    ("POST","https://api.dingtalk.com/v1.0/workflow/processInstances/comments"),
                    ("POST","http://api.dingtalk.com/v1.0/oauth2/accessToken"),
                    ("POST","https://api.dingtalk.com.evil.test/v1.0/oauth2/accessToken"),
                    ("POST","https://api.dingtalk.com:444/v1.0/oauth2/accessToken"),
                    ("DELETE","https://api.dingtalk.com/v1.0/workflow/processes/forecast")]
            for method,url in writes:
                with self.subTest(url=url), self.assertRaises(DingTalkError): client.request(method,url)
            self.assertEqual(len(received),accepted)

    def test_adapter_blocks_before_auth_and_ignores_live_env_flags(self):
        client=DiagnosticDingTalkClient(cfg(), transport=httpx.MockTransport(lambda r: self.fail("network")))
        with patch.object(client,"access_token",side_effect=AssertionError("auth")):
            for action in [lambda: client.start_process([],[],{},"x"), lambda: client.upload_approval_attachment(ROOT/"no-file"),
                           lambda: client._request("POST","https://api.dingtalk.com/v1.0/workflow/processInstances")]:
                with self.assertRaises(DingTalkError): action()
        self.assertFalse(client.config.live_submit_enabled)
        client.reload_config()
        self.assertFalse(client.config.live_submit_enabled)
        self.assertFalse(client.config.attachment_enabled)
        client._http.close()

    def test_redirect_to_write_never_follows(self):
        calls=[]
        with httpx.Client(transport=DiagnosticTransport(httpx.MockTransport(lambda r: (calls.append(r),httpx.Response(307,headers={"location":"https://api.dingtalk.com/v1.0/workflow/processInstances"}))[1])),follow_redirects=True) as client:
            with self.assertRaises(DingTalkError): client.post("https://api.dingtalk.com/v1.0/workflow/processes/forecast")
        self.assertEqual(len(calls),1)

    def test_actual_query_error_keeps_http_code_and_lowercase_requestid(self):
        client=DiagnosticDingTalkClient(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(400,json={"requestid":"query-trace","code":"badRequest","message":"错误"})))
        with self.assertRaises(DingTalkError):client._request("POST","https://api.dingtalk.com/v1.0/workflow/processes/forecast",token=False)
        failure=failure_record(client.last_failure,origin="钉钉本次只读查询响应")
        self.assertEqual(failure["trace_id"],"query-trace");self.assertEqual(failure["http_status"],400)
        self.assertFalse(failure["replayed"]);client._http.close()

class EvidenceTests(unittest.TestCase):
    def report(self, **kw): return build_report(form(),payload(),{"success":True,"nodes":[]},**kw)
    def codes(self,r): return {f["code"] for f in r["findings"]}

    def test_missing_selection_for_required_node(self):
        node={"name":"主管","kind":"manual","actor_type":"approver","actor_key":"a","required":True}
        r=build_report(form(),payload(),{"success":True,"nodes":[node]})
        self.assertIn("PERSON_REQUIRED",self.codes(r));self.assertEqual(r["personnel"][0]["role"],"自选审批人")

    def test_roles_order_and_selections_preserved(self):
        nodes=[{"name":str(i),"kind":kind,"actor_type":actor,"actor_key":str(i),"fixed_actioners":[{"user_id":"f","name":"固定"}]} for i,(kind,actor) in enumerate([("fixed","approver"),("manual","approver"),("fixed","audit"),("fixed","notifier")])]
        selections={"1":[{"user_id":"m","name":"选择","source":"local_default"}]};old=copy.deepcopy((nodes,selections))
        rows,_=inspect_people(nodes,selections)
        self.assertEqual([r["role"] for r in rows],["固定审批人","自选审批人","办理人","抄送人"])
        self.assertEqual((nodes,selections),old);self.assertEqual(rows[1]["users"][0]["source"],"local_default")

    def test_departure_marker_never_assumes_departed_or_replaces_user(self):
        node={"name":"财务","kind":"fixed","fixed_actioners":[{"user_id":"a","name":"某人(已离职)"}]}
        client=Mock();client.users_by_ids.return_value=[{"user_id":"a","name":"某人"}]
        rows, findings=inspect_people([node],{},client)
        self.assertTrue(rows[0]["users"][0]["departure_marker"]);self.assertEqual(rows[0]["users"][0]["user_id"],"a")
        self.assertEqual(findings[0]["level"],"unverified")

    def test_query_denial_and_missing_person_unverified(self):
        for result in [DingTalkError("无权限"),[]]:
            client=Mock();client.users_by_ids.side_effect=result if isinstance(result,Exception) else None;client.users_by_ids.return_value=result
            rows,_=inspect_people([{"name":"办理","kind":"fixed","actor_type":"audit","fixed_actioners":[{"user_id":"a","name":"某人"}]}],{},client)
            self.assertEqual(rows[0]["status"],"未验证");self.assertFalse(rows[0]["users"][0]["departure_marker"])

    def test_changed_nodes_are_local_findings(self):
        old=[{"activity_id":"old","name":"原节点"}]
        r=self.report(previous_nodes=old)
        self.assertIn("WORKFLOW_CHANGED",self.codes(r));self.assertIsNone(r["failure"])

    def test_amount_mismatch_and_calculation_pending(self):
        r=self.report(expected_amount="1200",pending_attachment=True)
        self.assertIn("TABLE_AMOUNT_MISMATCH",self.codes(r))
        f=next(f for f in r["findings"] if f["code"]=="CALCULATED_OMITTED")
        self.assertEqual(f["level"],"unverified");self.assertIn("ATTACHMENT_NOT_UPLOADED",self.codes(r))

    def test_bad_attachment_five_fields_and_size(self):
        for attachment in [{"fileId":"a","spaceId":"s","fileName":"a.xlsx"}, {"fileId":"a","spaceId":"s","fileName":"a.xlsx","fileType":"xlsx","fileSize":"NaN"}]:
            p=payload();p["formComponentValues"][-1]["value"]=json.dumps([attachment])
            self.assertIn("ATTACHMENT_FIELDS",self.codes(build_report(form(),p,{"success":True,"nodes":[]})))

    def test_explicit_empty_calculation_is_pending_without_placeholder(self):
        p=payload();p["formComponentValues"].append({"name":"总额","value":""});before=copy.deepcopy(p)
        r=build_report(form(),p,{"success":True,"nodes":[]},pending_attachment=True)
        self.assertIn("CALCULATED_REQUIRED_UNVERIFIED",self.codes(r))
        self.assertFalse(any(f["level"]=="error" and f["field"]=="总额" for f in r["findings"]))
        self.assertEqual(p,before)

    def test_conditional_detail_required_is_pending(self):
        s=form();content=json.loads(s["schemaContent"])
        content["items"][-1]["children"][0]["props"]["id"]="amount-cell"
        content["items"][0]["props"]["behaviorLinkage"]=[{"targets":[{"fieldId":"amount-cell"}]}]
        s["schemaContent"]=json.dumps(content);p=payload();p["formComponentValues"][1]["value"]="[[]]";p["formComponentValues"][1]["details"]=[]
        r=build_report(s,p,{"success":True,"nodes":[]},pending_attachment=True)
        self.assertTrue(any(f["code"]=="CONDITIONAL_REQUIRED" and f["field"]=="明细/分摊金额" and f["level"]=="unverified" for f in r["findings"]))

    def test_replay_has_real_trace_no_network_or_fabricated_instance(self):
        with patch.object(httpx.Client,"request",side_effect=AssertionError("network")):
            r=historical_replay(ROOT)
        self.assertEqual(r["failure"]["trace_id"],"01A12041-7490-736A-A0F3-9DC94F72A7A6")
        self.assertEqual(r["failure"]["error_code"],"processInstanceStartFailed")
        self.assertEqual(r["failure"]["http_status"],400);self.assertFalse(r["failure"]["has_instance_id"])
        self.assertFalse(r["failure"]["retried"]);self.assertEqual(r["real_create_calls"],0)

    def test_redacts_nested_json_account_secrets_and_links(self):
        data={"client_secret":"REPLACE_ME","token":"token123","bank_account":"1234567890","nested":json.dumps({"access_token":"REPLACE_ME","resourceUrls":["https://signed.test/a"]}),"fields":[{"name":"银行账号","value":"1234567890"}],"error":"access_token=secret123 https://signed.test/url 1234567890123456"}
        text=json.dumps(redact(data),ensure_ascii=False)
        for secret in ["secret123","token123","1234567890","nestedsecret","signed.test"]:self.assertNotIn(secret,text)

class DiagnosticAPITests(unittest.TestCase):
    def setUp(self):
        from app import main
        self.main=main;self.temp=tempfile.TemporaryDirectory(dir=ROOT/'output')
        self.db=Database(Path(self.temp.name)/'test.db');self.patcher=patch.object(main,'db',self.db);self.patcher.start()
        self.client=TestClient(main.app)

    def tearDown(self):self.patcher.stop();self.temp.cleanup()

    def task(self):
        from app.dingtalk import build_display_fields
        from app.legacy_chain import fingerprint
        group={"id":"g","company":"模拟","payment_department":"总经办","amount":100,"count":1,"file_name":"test.xlsx","file_path":"test.xlsx","department_allocations":[]}
        fields=build_display_fields(group,{},"7月","test.xlsx",self.main.dingtalk.config.effective_applicant_dept_id)
        task={"id":"diagnostic-only","status":"prepared","created_at":"now","updated_at":"now","file_name":"test.xlsx","source_path":"test.xlsx","output_root":"test","result":{"month":"7月","departments":[group]},"workflow_forecasts":{"g":{"nodes":[],"success":True,"signature":forecast_signature([]),"resolved_fields":fields,"source_fields_fingerprint":fingerprint(fields),"schema_snapshot":form()}}}
        self.db.save_task(task);return task

    def test_submit_and_retry_rejected_without_task_or_attempt_writes(self):
        with patch.object(self.main.dingtalk,"start_process",side_effect=AssertionError("create")):
            for suffix in ["submit","retry"]:
                r=self.client.post('/api/tasks/unknown/approvals/'+suffix,json={"confirm":True});self.assertEqual(r.status_code,409)
        self.assertEqual(self.db.list_tasks(),[])

    def test_preview_fields_added_and_diagnosis_does_not_mutate_task(self):
        task=self.task();before=self.db.get_task(task['id'])
        with patch.object(self.main.dingtalk,"get_schema",return_value=form()),patch.object(self.main.dingtalk,"forecast",return_value={}),patch.object(self.main.dingtalk,"originator_process_access",return_value={"allowed":True}):
            r=self.client.post('/api/tasks/'+task['id']+'/approvals/diagnostics')
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['read_only'])
        self.assertEqual(self.db.get_task(task['id']),before);self.assertEqual(self.db.approval_attempts(task['id']),[])
        items,_=self.main.preview_items(task);self.assertIn('diagnostics',items[0]);self.assertIn('diagnostic_summary',items[0]);self.assertIn('confirmation_fingerprint',items[0])

    def test_schema_change_and_read_denial_report_without_state_change(self):
        task=self.task();changed=form();changed['schemaContent']='{"items":[]}'
        with patch.object(self.main.dingtalk,'get_schema',return_value=changed),patch.object(self.main.dingtalk,'forecast',return_value={}),patch.object(self.main.dingtalk,'originator_process_access',side_effect=DingTalkError('无权限')):
            r=self.client.post('/api/tasks/'+task['id']+'/approvals/diagnostics').json()
        codes={f['code'] for f in r['items'][0]['diagnostics']['findings']}
        self.assertIn('TEMPLATE_CHANGED',codes);self.assertIn('ORIGINATOR_ACCESS_UNVERIFIED',codes)
        self.assertEqual(self.db.get_task(task['id'])['status'],'prepared')

    def test_replay_and_export_never_register_sample_as_task(self):
        r=self.client.get('/api/experiment/replay?export=true')
        self.assertEqual(r.status_code,200);self.assertIn('attachment',r.headers['content-disposition'])
        self.assertEqual(self.db.list_tasks(),[])

if __name__=='__main__': unittest.main()
