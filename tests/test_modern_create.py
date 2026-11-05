import json
import unittest
from dataclasses import replace
from unittest.mock import Mock
import httpx
from app.dingtalk import DingTalkClient, DingTalkConfig, DingTalkError, DingTalkUncertainSubmissionError
from app.modern_chain import build_modern_template_chain

class ModernCreateTest(unittest.TestCase):
    def setUp(self):
        self.client = DingTalkClient(DingTalkConfig(client_id="id", client_secret="REPLACE_ME", agent_id="123",
            corp_id="corp", originator_user_id="user", originator_dept_id="2", process_code="test", live_submit_enabled=True))
        self.addCleanup(self.client.close)
        self.manual = {"kind":"manual", "name":"经理", "actor_key":"manual_a", "required":True,
            "allowed_multi":False, "selection_range_restricted":True, "selection_range_resolvable":True,
            "allowed_user_ids":["u1", "u2"], "activity_id":"a"}
        self.fixed = {"kind":"fixed", "name":"财务", "activity_id":"f", "fixed_actioners":[{"user_id":"fixed1"}]}
        self.nodes=[self.manual,self.fixed]
        self.selections={"manual_a":[{"user_id":"u1"}]}
        self.fields=[{"name":"测试", "value":"0.01", "id":"x", "componentType":"TextField"}]
        self.client._request=Mock(return_value={"instanceId":"new-instance"})
    def call(self, **kw):
        return self.client.start_process(self.fields,self.nodes,self.selections,"local-only",**kw)
    def test_new_endpoint_and_target_select_only(self):
        self.assertEqual(self.call(),"new-instance")
        args,kw=self.client._request.call_args
        self.assertEqual(args,("POST","https://api.dingtalk.com/v1.0/workflow/processInstances"))
        self.assertTrue(kw["uncertain_on_transport"])
        self.assertEqual(kw["json"]["targetSelectActioners"],[{"actionerKey":"manual_a","actionerUserIds":["u1"]}])
        self.assertEqual(kw["json"]["formComponentValues"],[{"name":"测试","value":"0.01"}])
        for key in ["approvers","approvers_v2","ccList","ccPosition","requestId"]: self.assertNotIn(key,kw["json"])
        self.assertEqual(kw.get("retries",0),0)
    def test_fixed_only_omits_manual_parameter(self):
        self.nodes=[self.fixed];self.selections={};self.call()
        self.assertNotIn("targetSelectActioners",self.client._request.call_args.kwargs["json"])
    def test_create_keeps_table_rows_and_flat_details_without_leaking_internal_fields(self):
        rows = [[{"name":"金额", "value":"0.01"}], [{"name":"金额", "value":"0.02"}]]
        self.fields = [{"name":"明细", "value":json.dumps(rows), "componentType":"TableField",
                        "id":"internal-id", "details":[{"name":"stale", "value":"999"}]}]
        self.call()
        field = self.client._request.call_args.kwargs["json"]["formComponentValues"][0]
        self.assertEqual(json.loads(field["value"]), rows)
        self.assertEqual(field["details"], [rows[0][0], rows[1][0]])
        self.assertEqual(set(field), {"name", "value", "details"})
        self.client._request.reset_mock(return_value=True)
        self.client._request.return_value = {"result":{}}
        self.client.forecast(self.fields)
        predicted = self.client._request.call_args.kwargs["json"]["formComponentValues"][0]
        self.assertEqual(set(predicted), {"name", "value"})
    def test_empty_table_has_empty_details_and_invalid_table_never_sends(self):
        self.fields = [{"name":"未启用明细", "value":"[]", "componentType":"TableField"}]
        self.call()
        self.assertEqual(self.client._request.call_args.kwargs["json"]["formComponentValues"][0]["details"], [])
        self.client._request.reset_mock()
        for value in ('bad json', '{}', '[{}]', '[[1]]'):
            self.fields[0]["value"] = value
            with self.assertRaises(DingTalkError):
                self.call()
        self.client._request.assert_not_called()
    def test_required_and_range_errors_never_send(self):
        for selection in [[],[{"user_id":"outsider"}],[{"user_id":"u1"},{"user_id":"u2"}]]:
            self.selections={"manual_a":selection}
            with self.assertRaises(DingTalkError):self.call()
        self.client._request.assert_not_called()
    def test_stale_fingerprint_never_send(self):
        with self.assertRaises(DingTalkError):self.call(expected_chain_fingerprint="stale")
        self.client._request.assert_not_called()
    def test_unchanged_fingerprint_no_extra_legacy_consent(self):
        chain=build_modern_template_chain(self.nodes,self.selections)
        self.assertEqual(self.call(expected_chain_fingerprint=chain["fingerprint"]),"new-instance")
    def test_switch_and_missing_instance(self):
        self.client.config=replace(self.client.config,live_submit_enabled=False)
        with self.assertRaises(DingTalkError):self.call()
        self.client._request.assert_not_called()
        self.client.config=replace(self.client.config,live_submit_enabled=True)
        self.client._request.return_value={}
        with self.assertRaises(DingTalkUncertainSubmissionError):self.call()
    def test_no_retry_or_fallback_on_server_error(self):
        self.client._request.side_effect=DingTalkError("processInstanceStartFailed")
        with self.assertRaises(DingTalkError):self.call()
        self.assertEqual(self.client._request.call_count,1)
    def test_header_and_transport_timeout_single_attempt(self):
        calls=[]
        def handler(request):
            calls.append(request)
            self.assertEqual(request.headers["x-acs-dingtalk-access-token"],"fake-token")
            raise httpx.ReadTimeout("simulated",request=request)
        self.client.close();self.client._http=httpx.Client(transport=httpx.MockTransport(handler))
        self.client.access_token=Mock(return_value="fake-token")
        self.client._request=DingTalkClient._request.__get__(self.client)
        with self.assertRaises(DingTalkUncertainSubmissionError):self.call()
        self.assertEqual(len(calls),1)
    def test_template_does_not_flatten_parallel_or_intermediate_cc(self):
        nodes=[dict(self.fixed, activity_id="f1"),dict(self.fixed,activity_id="cc",actor_type="notifier"),dict(self.fixed,activity_id="f2")]
        chain=build_modern_template_chain(nodes,{})
        self.assertEqual(chain["mode"],"modern_template");self.assertEqual(chain["errors"],[])
        self.assertEqual(chain["approvers_v2"],[])

if __name__=="__main__":unittest.main()
