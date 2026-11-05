"""Authorized, independent one-shot calculation-field experiment. No app/env edits."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.dingtalk import (DingTalkClient, DingTalkError, DingTalkUncertainSubmissionError,
    load_dingtalk_config, form_values_from_schema, apply_local_role_scopes, forecast_signature)
from app.platform_dingtalk import normalize_forecast
from app.approval_cc import seed_cc_nodes, compatibility

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'calculated_1200_test_once_20261009'
TASK='1c9999999999999999ca2a46c19f8fa7'
GROUP='961f83680a82ac'
FIELD='付款金额含税（元）'
EXPECTED_NAME='副本 付款申请单11'
PROTECTED=['.env','app/dingtalk.py','app/modern_chain.py','data/workflow.db',
 'output/modern_create_once_guard.json','output/modern_live_report.json',
 'output/modern_create_request.json','output/modern_schema.json','output/modern_test_fixture.json']
def now():return datetime.now(timezone.utc).isoformat()
def write(name,data):
    temp=OUT/(name+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(OUT/name)
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in PROTECTED}

class OneCreateTransport(httpx.BaseTransport):
    def __init__(self,report,guard):
        self.inner=httpx.HTTPTransport(retries=0);self.report=report;self.guard=guard
    def handle_request(self,request):
        if request.method=='POST' and str(request.url)=='https://api.dingtalk.com/v1.0/workflow/processInstances':
            if not self.guard.exists() or self.report['create_calls']!=0:
                raise RuntimeError('一次创建限制已触发，未发送。')
            self.report.update(create_calls=1,status='creating',create_started_at=now())
            write('report.json',self.report)
            write('sent_request.json',json.loads(request.content))
            response=self.inner.handle_request(request)
            response.read()
            try:body=response.json()
            except ValueError:body={'unparsed_body':response.text[:1000]}
            write('create_response.json',{'http_status':response.status_code,'body':body,
                'trace_id':response.headers.get('x-acs-request-id') or response.headers.get('x-request-id')})
            return response
        return self.inner.handle_request(request)
    def close(self):self.inner.close()

def run(submit=False):
    OUT.mkdir(exist_ok=True)
    guard=OUT/'create_once_guard.json'
    if guard.exists():raise RuntimeError('本次授权实验已消耗；保留结果，不得重跑。')
    before=hashes()
    cfg=load_dingtalk_config()
    archived=json.loads((ROOT/'output/modern_live_report.json').read_text(encoding='utf-8'))
    if cfg.process_code!=archived['process_code'] or archived['template_name']!=EXPECTED_NAME:
        raise RuntimeError('当前配置不是已核对的专用副本模板。')
    con=sqlite3.connect((ROOT/'data/workflow.db').as_uri()+'?mode=ro',uri=True)
    try:
        if con.execute('select count(*) from approval_attempts where status in (?,?)',('queued','submitting')).fetchone()[0]:
            raise RuntimeError('存在其他提交任务，未开展测试。')
        task=json.loads(con.execute('select data_json from tasks where id=?',(TASK,)).fetchone()[0])
        scopes=[{'role_key':r[0],'role_name':r[1],'users':json.loads(r[2])} for r in con.execute('select role_key,role_name,users_json from approval_role_scopes') if not r[0].startswith('@')]
    finally:con.close()
    group=next(g for g in task['result']['departments'] if g['id']==GROUP)
    forecast=task['workflow_forecasts'][GROUP]
    selections=task['workflow_actioners'][GROUP]
    fields=forecast['resolved_fields']
    amount=Decimal(str(group['amount']))
    if amount!=Decimal('1200'):raise RuntimeError('金额与已获授权方案不一致。')
    attachment_path=(ROOT/group['file_path']).resolve()
    if not attachment_path.is_file():raise RuntimeError('原附件不存在，未上传。')
    report={'started_at':now(),'request_id':uuid.uuid4().hex,'template_name':EXPECTED_NAME,
            'amount':'1200','variant':'explicit_actual_calculated_value_only','create_calls':0,
            'attachment_upload_calls':0,'status':'preparing','steps':[],'authorization':'REPLACE_ME'}
    def stage(name):
        report['steps'].append({'name':name,'at':now()});write('report.json' if submit else 'preparation_report.json',report)
        print(name,flush=True)
    def normalize(raw):
        value=normalize_forecast(raw)
        apply_local_role_scopes(value['nodes'],scopes)
        seeded=seed_cc_nodes(value['nodes'],task.get('cc_defaults_snapshot') or {},selections)
        if seeded!=selections:raise RuntimeError('刷新后的抄送/人员选择与授权方案不同。')
        checked=compatibility(value['nodes'],selections)
        if not value.get('success') or checked['errors']:raise RuntimeError('预测或节点校验未通过：'+str(checked['errors']))
        if forecast_signature(value['nodes'])!=forecast['signature']:raise RuntimeError('预测节点与已确认任务不一致，未创建。')
        return value,checked['chain']
    client=DingTalkClient(replace(cfg,live_submit_enabled=False))
    client.close();client._http=httpx.Client(transport=OneCreateTransport(report,guard),timeout=30,follow_redirects=False)
    try:
        schema=client.get_schema(force_refresh=True)
        if schema.get('name')!=EXPECTED_NAME or schema.get('status')!='PUBLISHED' or schema.get('formCode')!=cfg.process_code:
            raise RuntimeError('专用模板名称、发布状态或processCode不匹配。')
        if not client.originator_process_access()['allowed']:raise RuntimeError('模板发起权限未通过。')
        stage('专用测试模板及发起权限核对通过')
        values=form_values_from_schema(schema,fields,include_calculated=False)
        initial,chain=normalize(client.forecast(values))
        preview=next(p for p in task['previews'] if p['group_id']==GROUP)
        if chain['fingerprint']!=preview['legacy_chain']['fingerprint']:
            raise RuntimeError('流程或人员完整信息与已确认预览不一致，未创建。')
        stage('当前流程及3个手选节点与已确认预览一致')
        if not submit:
            report.update(status='ready_not_submitted',protected_unchanged=before==hashes())
            write('preparation_report.json',report)
            return report
        with guard.open('x',encoding='utf-8') as stream:
            json.dump({'request_id':report['request_id'],'authorized_at':now(),'template_name':EXPECTED_NAME,'amount':'1200','max_create_calls':1},stream,ensure_ascii=False)
        report['attachment_upload_calls']=1;stage('开始上传原有部门附件')
        attachment=client.upload_approval_attachment(attachment_path)
        write('attachment.json',attachment);stage('原有部门附件上传成功')
        basevalues=form_values_from_schema(schema,fields,attachment,include_calculated=False)
        latest,chain=normalize(client.forecast(basevalues))
        if chain['fingerprint']!=preview['legacy_chain']['fingerprint']:raise RuntimeError('附件上传后完整流程变化，未创建。')
        candidatevalues=form_values_from_schema(schema,fields,attachment,include_calculated=True)
        baseline=client.prepare_start_payload(basevalues,latest['nodes'],selections,expected_chain_fingerprint=chain['fingerprint'])
        candidate=client.prepare_start_payload(candidatevalues,latest['nodes'],selections,expected_chain_fingerprint=chain['fingerprint'])
        old={v['name']:v for v in baseline['formComponentValues']};new={v['name']:v for v in candidate['formComponentValues']}
        if set(new)-set(old)!={FIELD} or any(new[k]!=v for k,v in old.items()):raise RuntimeError('表单变化超出唯一计算字段，未创建。')
        if any(candidate[k]!=v for k,v in baseline.items() if k!='formComponentValues'):raise RuntimeError('其他创建参数发生变化，未创建。')
        if Decimal(new[FIELD]['value'])!=amount:raise RuntimeError('计算字段金额不一致，未创建。')
        total=sum((Decimal(cell['value']) for name in ['项目分摊明细','部门分摊明细'] for row in json.loads(new[name]['value']) for cell in row if cell['name']=='金额（元）'),Decimal(0))
        if total!=amount:raise RuntimeError('明细金额合计不一致，未创建。')
        write('baseline_with_current_attachment.json',baseline);write('candidate_with_current_attachment.json',candidate)
        stage('提交前校验通过：仅增加计算控件1200，明细及附件保留')
        client.config=replace(client.config,live_submit_enabled=True)
        instance=client.start_process(candidatevalues,latest['nodes'],selections,report['request_id'],expected_chain_fingerprint=chain['fingerprint'])
        report.update(status='submitted',instance_id=instance)
        stage('钉钉已返回审批实例ID')
    except DingTalkUncertainSubmissionError as exc:report.update(status='uncertain',error=str(exc))
    except Exception as exc:report.update(status='failed' if report['create_calls'] else 'blocked_before_create',error=str(exc))
    finally:client.close()
    report.update(finished_at=now(),protected_unchanged=before==hashes())
    write('report.json' if submit else 'preparation_report.json',report)
    return report

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--submit-once',action='store_true');args=parser.parse_args()
    result=run(args.submit_once)
    print(json.dumps(result,ensure_ascii=False,indent=2))
