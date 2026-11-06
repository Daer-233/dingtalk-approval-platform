import asyncio
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from openpyxl import Workbook
from starlette.datastructures import UploadFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from payment_mcp.app import create_app
from payment_mcp.auth import Actor,PaymentError,sign_claims,verify
from payment_mcp.backend import Backend
from payment_mcp.config import Settings,TOOLS
from payment_mcp.files import stage
from payment_mcp.service import PaymentService
from payment_mcp.store import Store


def workbook():
    w=Workbook(); s=w.active
    s.append(['公司主体','姓名','一级部门','二级部门','三级部门','日常餐补','宵夜餐补','出差餐补'])
    s.append(['深圳再造','验收员工甲','财务管理中心','','',100,20,0])
    s.append(['深圳再造','验收员工乙','供应链中心','','',60,10,30])
    s.append(['深圳再造','验收员工丙','供应链中心','','',40,10,0])
    stream=io.BytesIO(); w.save(stream); return stream.getvalue()


def settings(path):
    return Settings(enabled=True,pilot_staff_id='pilot',corp_id='corp',processor_ids=('processor',),
                    host_secret='test-secret-'*5,web_url='http://192.168.1.20:5173',data_root=Path(path))


def claims(cfg,**updates):
    now=int(time.time())
    result={'generation':cfg.generation,'iss':'work-knowledge-payment-host','staff':cfg.pilot_staff_id,'corp':cfg.corp_id,
        'workspace':'ws','event':'event','grant':uuid4().hex,'iat':now,'exp':now+900,
        'target_type':'person','bot':cfg.bot_id,'processor':'processor'}
    result.update(updates); return result


class Platform:
    def __init__(self):
        self.calls=[]; self.tasks={}; self.fail_import=False
    async def close(self): pass
    async def request(self,method,task_id=None,operation='',**kw):
        self.calls.append((method,operation,task_id))
        assert operation not in ('submit','retry','actioners')
        if operation=='import':
            if self.fail_import: raise PaymentError('backend_uncertain','测试超时',503)
            task_id=uuid4().hex
            self.tasks[task_id]={'id':task_id,'status':'review','file_name':'测试.xlsx',
                'analysis':{'groups':[{'id':'g1','company':'深圳再造','suggested_department':'财务管理中心','known_rule':True,'count':1,'amount':120},
                                      {'id':'g2','company':'深圳再造','suggested_department':'供应链中心','known_rule':True,'count':2,'amount':150}]},
                'choices':[],'result':None,'previews':[], 'bank_account':'SECRET-ACCOUNT',
                'source_path':'C:/secret/employee.xlsx','employees':[{'name':'SECRET-PERSON'}]}
        raw=self.tasks[task_id]
        if operation=='groups': raw['choices']=kw['json']['groups']
        if operation=='prepare': raw['result']={'departments':[dict(g,payment_department=g['suggested_department']) for g in raw['analysis']['groups']]}
        if operation=='forecast': raw['workflow_forecasts']={'g1':{'nodes':[]}}
        if operation=='preview': raw['previews']=[{'group_id':g['id'],'valid':False,'nodes':[{'name':'财务审核','kind':'manual','required':True}],
                                                     'errors':['尚未选择审批人'],'fields':[{'bank':'SECRET-ACCOUNT'}]} for g in raw['analysis']['groups']]
        return copy.deepcopy(raw)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.cfg=settings(self.temp.name)
        self.store=Store(self.cfg.data_root); self.platform=Platform()
        self.service=PaymentService(lambda:self.cfg,self.store,self.platform)
        self.token=claims(self.cfg); self.actor=verify('Bearer '+sign_claims(self.token,self.cfg.host_secret),self.cfg,self.store,registering=True)
        self.store.register_run(self.actor)
    async def asyncTearDown(self): self.temp.cleanup()
    async def imported(self):
        a=await stage(UploadFile(io.BytesIO(workbook()),filename='测试.xlsx'),self.actor,self.cfg,self.store)
        return a,await self.service.import_excel(a['attachment_ref'],self.actor)
    async def test_import_idempotency_and_private_projection(self):
        a,result=await self.imported(); again=await self.service.import_excel(a['attachment_ref'],self.actor)
        self.assertEqual(result['task_id'],again['task_id'])
        self.assertEqual(sum(op=='import' for _,op,_ in self.platform.calls),1)
        text=json.dumps(result)
        for value in ['SECRET-ACCOUNT','SECRET-PERSON','source_path','employees']: self.assertNotIn(value,text)
        self.assertEqual([g['amount'] for g in result['groups']],['120.00','150.00'])
    async def test_event_duplicate_and_conflict(self):
        a=await stage(UploadFile(io.BytesIO(workbook()),filename='测试.xlsx'),self.actor,self.cfg,self.store)
        b=await stage(UploadFile(io.BytesIO(workbook()),filename='测试.xlsx'),self.actor,self.cfg,self.store)
        self.assertEqual(a['attachment_ref'],b['attachment_ref'])
        with self.assertRaises(PaymentError): await stage(UploadFile(io.BytesIO(workbook()+b'changed'),filename='测试.xlsx'),self.actor,self.cfg,self.store)
    async def test_foreign_and_unknown_tasks(self):
        a,r=await self.imported()
        other=replace(self.actor,staff='other')
        with self.assertRaises(PaymentError): self.store.attachment(a['attachment_ref'],other)
        with self.assertRaises(PaymentError): self.store.own(r['task_id'],other)
        with self.assertRaises(PaymentError): await self.service.get_task(uuid4().hex,self.actor)
        self.assertEqual(len((await self.service.list_tasks(self.actor))['tasks']),1)
    async def test_prepare_once_no_mapping_write_no_submit(self):
        _,r=await self.imported(); p=await self.service.prepare_preview(r['task_id'],self.actor)
        await self.service.prepare_preview(r['task_id'],self.actor)
        self.assertEqual(sum(op=='prepare' for _,op,_ in self.platform.calls),1)
        self.assertEqual(p['status'],'needs_review')
        self.assertTrue(all(not c['remember_mapping'] for c in self.platform.tasks[r['task_id']]['choices']))
        self.assertIn('?task_id=',p['review_url'])
        self.assertNotIn('SECRET-ACCOUNT',json.dumps(p))
    async def test_unknown_department_requires_web(self):
        _,r=await self.imported(); self.platform.tasks[r['task_id']]['analysis']['groups'][0]['known_rule']=False
        result=await self.service.prepare_preview(r['task_id'],self.actor)
        self.assertEqual(result['status'],'needs_review')
        self.assertFalse(any(op=='prepare' for _,op,_ in self.platform.calls))
    async def test_existing_submission_never_modified(self):
        _,r=await self.imported(); self.platform.tasks[r['task_id']]['approval_attempts']=[{'status':'uncertain'}]
        await self.service.prepare_preview(r['task_id'],self.actor)
        self.assertFalse(any(op=='prepare' for _,op,_ in self.platform.calls))
    async def test_import_timeout_and_restart_no_retry(self):
        a=await stage(UploadFile(io.BytesIO(workbook()),filename='测试.xlsx'),self.actor,self.cfg,self.store)
        self.platform.fail_import=True
        with self.assertRaises(PaymentError): await self.service.import_excel(a['attachment_ref'],self.actor)
        restored=Store(self.cfg.data_root); restored.recover()
        service=PaymentService(lambda:self.cfg,restored,self.platform)
        with self.assertRaises(PaymentError): await service.import_excel(a['attachment_ref'],self.actor)
        self.assertEqual(sum(op=='import' for _,op,_ in self.platform.calls),1)
    async def test_interrupted_operation_recovered_as_uncertain(self):
        self.store.begin('interrupted',self.actor,'import'); Store(self.cfg.data_root).recover()
        with self.assertRaises(PaymentError) as e: self.store.begin('interrupted',self.actor,'import')
        self.assertEqual(e.exception.code,'uncertain')
    async def test_disable_and_revoke_immediate(self):
        _,r=await self.imported(); self.store.revoke(self.actor)
        with self.assertRaises(PaymentError): await self.service.get_task(r['task_id'],self.actor)
        self.cfg=replace(self.cfg,enabled=False)
        with self.assertRaises(PaymentError): await self.service.list_tasks(self.actor)
    async def test_files_validation(self):
        for name,body in [('x.xls',workbook()),('../x.xlsx',workbook()),('x.xlsx',b'ENCRYPTED OLE')]:
            with self.subTest(name=name),self.assertRaises(PaymentError):
                await stage(UploadFile(io.BytesIO(body),filename=name),self.actor,self.cfg,self.store)
        with self.assertRaises(PaymentError):
            await stage(UploadFile(io.BytesIO(workbook()),filename='x.xlsx'),self.actor,replace(self.cfg,max_bytes=100),self.store)
    async def test_bad_department_choices(self):
        _,r=await self.imported()
        for choices in [[{'group_id':'injected','payment_department':'x'}],[{'group_id':'g1','payment_department':'x','actor':'other'}]]:
            with self.assertRaises(PaymentError): await self.service.prepare_preview(r['task_id'],self.actor,choices)


class AuthorizationTests(unittest.TestCase):
    def test_old_grant_does_not_revive_after_off_on(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=settings(temp);store=Store(cfg.data_root)
            header='Bearer '+sign_claims(claims(cfg),cfg.host_secret)
            actor=verify(header,cfg,store,registering=True);store.register_run(actor)
            self.assertEqual(verify(header,cfg,store).staff,'pilot')
            with self.assertRaises(PaymentError):verify(header,replace(cfg,enabled=False,generation=1),store)
            with self.assertRaises(PaymentError):verify(header,replace(cfg,enabled=True,generation=2),store)

    def test_wrong_actor_company_group_processor_and_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=settings(temp); store=Store(cfg.data_root)
            for updates in [{'staff':'someone'},{'corp':'other'},{'target_type':'group'},{'processor':'other'},{'exp':0},{'bot':'other'}]:
                with self.subTest(updates=updates),self.assertRaises(PaymentError):
                    verify('Bearer '+sign_claims(claims(cfg,**updates),cfg.host_secret),cfg,store,registering=True)
            with self.assertRaises(PaymentError): verify('Bearer '+sign_claims(claims(cfg),'bad'),cfg,store,registering=True)


class TransportTests(unittest.TestCase):
    def test_real_mcp_transport_and_revocation(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=settings(temp); platform=Platform(); app=create_app(lambda:cfg,platform)
            token=sign_claims(claims(cfg),cfg.host_secret)
            headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
            with TestClient(app) as client:
                self.assertEqual(client.get('/health').json()['can_submit'],False)
                self.assertEqual(client.post('/internal/runs',headers=headers).status_code,200)
                catalog=client.get('/internal/tools',headers=headers).json()['tools']
                self.assertEqual({t['name'] for t in catalog},set(TOOLS))
                r=client.post('/internal/attachments',headers=headers,files={'file':('test.xlsx',workbook())})
                self.assertEqual(r.status_code,200,r.text); ref=r.json()['attachment_ref']
                for number,method,params in [(1,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}),
                    (2,'tools/list',{}),(3,'tools/call',{'name':'payment_import_excel','arguments':{'attachment_ref':ref}})]:
                    r=client.post('/mcp/',headers=headers,json={'jsonrpc':'2.0','id':number,'method':method,'params':params})
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertNotIn('error',r.json(),r.text)
                    if method=='tools/call': self.assertFalse(r.json()['result'].get('isError'),r.text)
                r=client.post('/mcp/',headers=headers,json={'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'payment_submit','arguments':{}}})
                self.assertTrue(r.json().get('error') or r.json().get('result',{}).get('isError'))
                self.assertEqual(client.post('/internal/runs/revoke',headers=headers).status_code,200)
                self.assertEqual(client.get('/internal/tools',headers=headers).status_code,403)
                self.assertEqual(client.get('/internal/tools').status_code,403)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_submit_or_global_configuration_paths(self):
        seen=[]
        async def response(req): seen.append(req.url.path); return httpx.Response(200,json={'id':'a'*32})
        with tempfile.TemporaryDirectory() as temp:
            cfg=settings(temp)
            async with httpx.AsyncClient(base_url=cfg.backend_url,transport=httpx.MockTransport(response)) as client:
                b=Backend(cfg,client)
                for op in ['submit','retry','actioners','company-profiles']:
                    with self.assertRaises(PaymentError): await b.request('POST','a'*32,op)
                self.assertEqual(seen,[])
                await b.request('GET','a'*32)
                self.assertEqual(seen,['/api/tasks/'+'a'*32])


if __name__=='__main__': unittest.main()
