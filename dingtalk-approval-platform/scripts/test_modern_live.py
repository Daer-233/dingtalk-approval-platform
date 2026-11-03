"""One-shot live test for the isolated modern API copy. Default: read-only preparation."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook
from app.dingtalk import (DingTalkClient, DingTalkError, DingTalkUncertainSubmissionError,
    form_values_from_schema, normalize_forecast, forecast_signature, validate_node_actioners, wire_form_values, apply_local_role_scopes)
from app.modern_chain import build_modern_template_chain

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"output"
def now(): return datetime.now(timezone.utc).isoformat()
def write(path,value):
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(path)
def run(submit):
    if (OUT/"modern_create_once_guard.json").exists():
        print("本副本已执行过一次性测试，禁止再次创建或覆盖结果。请查看 output/modern_live_report.json。")
        return
    fixture=json.loads((OUT/"modern_test_fixture.json").read_text(encoding="utf-8"))
    assert fixture["test_only"] and fixture["amount"]=="0.01"
    fields=fixture["fields"]
    assert "请勿付款" in next(f["value"] for f in fields if f["name"]=="付款事由")
    client=DingTalkClient()
    report={"started_at":now(),"endpoint":"https://api.dingtalk.com/v1.0/workflow/processInstances",
            "request_id":uuid.uuid4().hex,"amount":"0.01","synthetic_input":True,"create_calls":0,
            "status":"preparing","steps":[]}
    path=OUT/"modern_live_report.json"
    def stage(name,**extra):
        report["steps"].append({"name":name,"at":now(),**extra});write(path,report)
    try:
        schema=client.get_schema(force_refresh=True)
        name=str(schema.get("name")or"")
        report["template_name"]=name;report["process_code"]=client.config.process_code
        if not any(word in name for word in ("副本","测试")) or schema.get("status")!="PUBLISHED":
            raise DingTalkError("安全检查：仅允许已发布的副本/测试审批模板。")
        access=client.originator_process_access()
        if not access["allowed"]:raise DingTalkError("固定发起人不在模板可发起范围内。")
        stage("模板与发起权限核对通过")
        values=form_values_from_schema(schema,fields,include_calculated=False)
        forecast=normalize_forecast(client.forecast(values))
        if not forecast.get("success"):raise DingTalkError("流程预测失败："+str(forecast))
        from app.db import Database
        scopes=Database(ROOT/"data/workflow.db").list_approval_role_scopes()
        nodes=apply_local_role_scopes(forecast["nodes"],scopes)
        selections={k:v for k,v in fixture["node_actioners"].items() if k in {n.get("actor_key") for n in nodes}}
        errors=validate_node_actioners(nodes,selections)+build_modern_template_chain(nodes,selections)["errors"]
        if errors:raise DingTalkError("；".join(errors))
        report["nodes"]=[{"name":n.get("name"),"kind":n.get("kind"),"actor_key":n.get("actor_key"),
                          "selected_count":len(selections.get(n.get("actor_key"),[]))} for n in nodes]
        stage("预测与手选节点校验通过",node_count=len(nodes))
        write(OUT/"modern_prepared_request.json",{"originatorUserId":client.config.originator_user_id,
             "processCode":client.config.process_code,"deptId":int(client.config.originator_dept_id),
             "formComponentValues":wire_form_values(values),"node_actioners":selections})
        if not submit:
            report.update(status="ready_not_submitted",finished_at=now());write(path,report);print(json.dumps(report,ensure_ascii=False,indent=2));return
        # Persist before any live mutation. Refuse reruns, even after timeout/restart.
        guard=OUT/"modern_create_once_guard.json"
        with guard.open("x",encoding="utf-8") as stream:
            json.dump({"request_id":report["request_id"],"started_at":now(),"template_name":name},stream,ensure_ascii=False)
        wb=Workbook();ws=wb.active;ws.title="接口测试（勿付款）"
        ws.append(["用途","测试金额（元）","提示"])
        ws.append(["新版创建审批接口兼容性测试",0.01,"虚拟账号，请勿付款；不是业务餐补数据"])
        attachment_path=OUT/"新接口兼容性测试_0.01元.xlsx";wb.save(attachment_path)
        attachment=client.upload_approval_attachment(attachment_path)
        stage("测试附件上传成功",filename=attachment_path.name)
        final_values=form_values_from_schema(schema,fields,attachment,include_calculated=False)
        latest=normalize_forecast(client.forecast(final_values))
        latest["nodes"]=apply_local_role_scopes(latest.get("nodes")or[],scopes)
        if not latest.get("success") or forecast_signature(nodes)!=forecast_signature(latest.get("nodes")or[]):
            raise DingTalkError("上传附件后流程节点变化，未发起审批，请重新核对。")
        stage("上传后再次预测一致")
        # Record request locally with no access token/client secret. Avoid logging headers.
        from app.dingtalk import build_target_select_actioners
        write(OUT/"modern_create_request.json",{"originatorUserId":client.config.originator_user_id,
             "processCode":client.config.process_code,"deptId":int(client.config.originator_dept_id),
             "microappAgentId":int(client.config.agent_id),"formComponentValues":wire_form_values(final_values,include_details=True),
             "targetSelectActioners":build_target_select_actioners(nodes,selections)})
        client.config=replace(client.config,live_submit_enabled=True) # in memory only, .env remains false
        report.update(status="creating",create_calls=1);stage("发起单条新接口测试")
        instance=client.start_process(final_values,latest["nodes"],selections,report["request_id"],
                  expected_chain_fingerprint=build_modern_template_chain(latest["nodes"],selections)["fingerprint"])
        report.update(status="submitted",instance_id=instance,submitted_at=now())
        stage("钉钉已返回审批实例ID")
    except DingTalkUncertainSubmissionError as exc:
        report.update(status="uncertain",error=str(exc))
    except FileExistsError:
        # Preserve the prior authoritative result; a rerun must not hide it.
        print("此前已执行一次性测试；禁止重复创建，请查看 modern_live_report.json。")
        return
    except Exception as exc:
        report.update(status="failed" if report["create_calls"] else "blocked_before_create",error=str(exc))
    finally:
        client.close()
    report["finished_at"]=now();write(path,report);print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--submit-once",action="store_true",help="授权创建一条0.01元测试审批（不可重复执行）")
    run(parser.parse_args().submit_once)
