"""Use the confirmed synthetic workbook against the existing platform; never submit."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from starlette.datastructures import UploadFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/payment-mcp'))
from payment_mcp.auth import Actor
from payment_mcp.backend import Backend
from payment_mcp.config import Settings
from payment_mcp.files import stage
from payment_mcp.service import PaymentService
from payment_mcp.store import Store


async def main():
    base=Settings.load()
    if not base.pilot_staff_id or not base.corp_id:raise RuntimeError('Verify the pilot identity first')
    cfg=replace(base,enabled=True,data_root=ROOT/'data/payments/acceptance/real-platform-store')
    store=Store(cfg.data_root)
    actor=Actor(cfg.pilot_staff_id,cfg.corp_id,'internal-acceptance','synthetic-preview-v1',uuid4().hex,int(time.time())+900,cfg.generation)
    store.register_run(actor)
    paths=[]
    class Recorder(httpx.AsyncHTTPTransport):
        async def handle_async_request(self,request):
            paths.append({'method':request.method,'path':request.url.path})
            if '/submit' in request.url.path or '/retry' in request.url.path:
                raise AssertionError('Approval submission must never be invoked')
            return await super().handle_async_request(request)
    client=httpx.AsyncClient(base_url=cfg.backend_url,transport=Recorder(),timeout=120,trust_env=False,follow_redirects=False)
    service=PaymentService(lambda:cfg,store,Backend(cfg,client))
    import importlib.util
    spec=importlib.util.spec_from_file_location('payment_fixture',ROOT/'scripts/make-payment-test-workbook.py')
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    workbook=ROOT/'data/payments/acceptance'/fixture.NAME
    fixture.validate_fixture(workbook)
    with workbook.open('rb') as handle:
        attachment=await stage(UploadFile(handle,filename=workbook.name),actor,cfg,store)
    result=await service.import_excel(attachment['attachment_ref'],actor)
    task_id=result['task_id']
    raw=await client.get('/api/tasks/'+task_id);raw.raise_for_status();task=raw.json()
    print('analysis_fields',list((task.get('analysis') or {}).keys()))
    print('group_fields',list(((task.get('analysis') or {}).get('groups') or [{}])[0].keys()))
    assert len(task.get('approval_attempts') or [])==0
    assert sum(g['count'] for g in result['groups'])==3,result['groups']
    assert sum(float(g['amount']) for g in result['groups'])==270,result['groups']
    prepared=await service.prepare_preview(task_id,actor)
    raw=await client.get('/api/tasks/'+task_id);raw.raise_for_status();final=raw.json()
    assert len(final.get('approval_attempts') or [])==0
    assert len(prepared['groups'])==2
    assert sum(g['count'] for g in prepared['groups'])==3
    assert sum(float(g['amount']) for g in prepared['groups'])==270
    report={'status':'passed','task_id':task_id,'review_url':prepared['review_url'],
            'groups':prepared['groups'],'preview_status':prepared['status'],'needs_review':prepared['needs_review'],
            'request_paths':paths,'approval_create_calls':0,'approval_attempts':0,'employee_messages_sent':0,
            'synthetic':True,'checked_at':time.time()}
    target=ROOT/'data/payments/acceptance/platform-acceptance.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    await client.aclose();store.revoke(actor)
    print(json.dumps({k:report[k] for k in ('status','preview_status','approval_create_calls','approval_attempts','employee_messages_sent')}))


if __name__=='__main__':asyncio.run(main())