"""Run in LangBot. Exercise actual grants with synthetic events; deliver no messages."""
import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,patch

from langbot.pkg.platform.adapters.dingtalk.knowledge_card import payment_host as host
from langbot.pkg.platform.adapters.dingtalk.knowledge_card.model import CardState
from langbot.pkg.agent.runner.session_registry import AgentRunSessionRegistry
from langbot.pkg.plugin import handler as handler_module

CFG={'enabled':True,'pilot_staff_id':'pilot','corp_id':'corp','bot_id':'bot','processor_ids':['processor'],
     'host_secret':'test-host-secret-'*4,'service_url':'http://127.0.0.1:8070'}
Adapter=type('DingTalkAdapter',(),{'__module__':'langbot.pkg.platform.adapters.dingtalk.adapter'})


def fake_event(text='付款预览',files=None):
    incoming=NS(sender_staff_id='pilot',sender_corp_id='corp',chatbot_corp_id='corp',conversation_type='1',message_id='original-id')
    platform=NS(incoming_message=incoming,file=None,name='',quoted_message=None)
    source=NS(source_platform_object=platform)
    class Input:
        def __init__(self):self.text=text;self.contents=[{'type':'text','text':text}];self.attachments=files or []
        def model_copy(self,update):
            new=copy.copy(self)
            for key,value in update.items():setattr(new,key,value)
            return new
    class Event:
        def model_copy(self,update):
            new=copy.copy(self)
            for key,value in update.items():setattr(new,key,value)
            return new
    event=Event();event.bot_id='bot';event.workspace_id='ws';event.source='platform';event.event_type='message.received'
    event.event_id='event';event.actor=NS(actor_type='user',actor_id='pilot');event.input=Input();event.delivery=NS(reply_target={'target_type':'person','message_id':'m'})
    binding=NS(processor_id='processor',runner_id=host.RUNNER)
    context={'_platform_event':source,'_delivery_adapter':Adapter()}
    return event,binding,context,source


def scope():return host.Scope(copy.deepcopy(CFG),'pilot','ws','processor','event','original-id',available=True)


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg_patch=patch.object(host,'settings',return_value=copy.deepcopy(CFG));self.cfg_patch.start()
        self.catalog_patch=patch.object(host,'catalog',return_value=json.loads(Path('/app/data/work-payment-catalog.json').read_text())['tools']);self.catalog_patch.start()
        self.internal_patch=patch.object(host,'internal',new=AsyncMock(return_value={'status':'registered'}));self.internal=self.internal_patch.start()
        self.revoke_patch=patch.object(host,'revoke_token',new=AsyncMock());self.revoke=self.revoke_patch.start()
        host.RUNS.clear()
    async def asyncTearDown(self):
        host.RUNS.clear();self.cfg_patch.stop();self.catalog_patch.stop();self.internal_patch.stop();self.revoke_patch.stop()
    async def test_verified_private_identity_only(self):
        e,b,c,s=fake_event();self.assertIsNotNone(host._select(e,b,c,CFG))
        for field,value in [('sender_staff_id','other'),('sender_corp_id','other'),('chatbot_corp_id','other'),('conversation_type','2')]:
            old=getattr(s.source_platform_object.incoming_message,field);setattr(s.source_platform_object.incoming_message,field,value)
            self.assertIsNone(host._select(e,b,c,CFG));setattr(s.source_platform_object.incoming_message,field,old)
        e.source='api';self.assertIsNone(host._select(e,b,c,CFG));e.source='platform'
        e.delivery.reply_target['target_type']='group';self.assertIsNone(host._select(e,b,c,CFG))
    async def test_untrusted_download_and_multiple_files(self):
        for url in ['http://127.0.0.1/x','https://evil.example/x','file:///c:/secret','https://dingtalk.com.evil.example/x']:
            with self.assertRaises(ValueError):host.validate_download_url(url)
        e,b,c,s=fake_event(files=[{'type':'file'},{'type':'file'}])
        with self.assertRaises(ValueError):host.file_inputs(s,e)
    async def test_tools_preserve_original_resources_and_respect_manifest(self):
        original={'tools':[],'knowledge_bases':[{'kb_id':'existing'}],'skills':[],'models':[]}
        class Builder:
            async def build_resources_from_binding(self,*args):return copy.deepcopy(original)
        host.install_builder(Builder)
        e,b,c,s=fake_event();descriptor=NS(permissions=NS(tools=['detail','call']))
        token=host.ACTIVE.set(scope())
        try:
            with patch('langbot.pkg.agent.runner.config_schema.uses_host_tools',return_value=True):
                result=await Builder().build_resources_from_binding(None,e,b,descriptor)
                self.assertEqual({t['tool_name'] for t in result['tools']},host.NAMES)
                self.assertEqual(result['knowledge_bases'],original['knowledge_bases'])
                descriptor.permissions.tools=['detail']
                result=await Builder().build_resources_from_binding(None,e,b,descriptor)
                self.assertEqual(result['tools'],[])
        finally:host.ACTIVE.reset(token)
    async def test_actual_registry_grants_plugin_identity_and_revocation(self):
        class Registry(AgentRunSessionRegistry):pass
        host.install_registry(Registry);reg=Registry();s=scope()
        resources={'models':[],'knowledge_bases':[],'tools':[{'tool_name':'payment_get_task','source':'platform','source_id':'payment_get_task',
            'operations':['detail','call'],'parameters':{'type':'object','properties':{'task_id':{'type':'string'}},'required':['task_id'],'additionalProperties':False}}]}
        token=host.ACTIVE.set(s)
        try:
            await reg.register(run_id='run',runner_id=host.RUNNER,query_id='query',plugin_identity='langbot-team/CodexAgent',
                               resources=resources,bot_id='bot',workspace_id='ws')
        finally:host.ACTIVE.reset(token)
        with patch.object(handler_module,'get_session_registry',return_value=reg):
            app=NS(logger=logging.getLogger('test'))
            session,error=await handler_module._validate_run_authorization('run','tool','payment_get_task',app,'langbot-team/CodexAgent',operation='call')
            self.assertIsNone(error)
            source,error=handler_module._validate_frozen_tool_source_identity(session,'payment_get_task',app)
            self.assertIsNone(error);self.assertEqual(source,{'source':'platform','source_id':'payment_get_task'})
            _,error=await handler_module._validate_run_authorization('run','tool','payment_get_task',app,'other-plugin',operation='call')
            self.assertIsNotNone(error)
            _,error=await handler_module._validate_run_authorization('run','tool','payment_submit',app,'langbot-team/CodexAgent',operation='call')
            self.assertIsNotNone(error)
            with patch.object(host,'mcp_call',new=AsyncMock(return_value={'status':'needs_review'})) as call:
                result=await host.execute_payment_tool(session,'payment_get_task',{'task_id':'a'*32})
                self.assertEqual(result['status'],'needs_review');self.assertEqual(call.await_count,1)
                rejected=await host.execute_payment_tool(session,'payment_get_task',{'task_id':'a'*32,'staff':'other'})
                self.assertEqual(rejected['status'],'invalid_parameters')
            await reg.unregister('run')
            self.assertNotIn('run',host.RUNS);self.assertEqual(self.revoke.await_count,1)
            with self.assertRaises(PermissionError):await host.execute_payment_tool(session,'payment_get_task',{'task_id':'a'*32})
    async def test_new_generation_never_revives_old_scope(self):
        s=scope();self.assertTrue(host.current_enabled(s))
        host.settings.return_value={**CFG,'generation':2}
        self.assertFalse(host.current_enabled(s))

    async def test_handler_delegates_nonpayment_tools(self):
        original=AsyncMock(return_value={'original':True});module={'execute_platform_tool':original}
        host.install_handler(module)
        result=await module['execute_platform_tool'](None,None,{},'event_get_actor',{})
        self.assertEqual(result,{'original':True});self.assertEqual(original.await_count,1)
    async def test_file_without_purpose_never_starts_model_or_import(self):
        class Orchestrator:
            async def run(self,*args):
                raise AssertionError('File-only input must ask first')
                yield
        host.install_orchestrator(Orchestrator)
        e,b,c,s=fake_event(text='');s.source_platform_object.file='https://api.dingtalk.com/file';s.source_platform_object.name='test.xlsx'
        with patch.object(host,'stage_file',new=AsyncMock(return_value={'attachment_ref':'r','file_name':'test.xlsx'})):
            output=[x async for x in Orchestrator().run(e,b,adapter_context=c)]
        self.assertTrue(output[-1].is_final);self.assertIn('尚未创建付款任务',output[-1].all_content)
        self.assertFalse(any('mcp' in str(x) for x in self.internal.await_args_list))
    async def test_generator_context_survives_streaming(self):
        marker=host.ContextVar('existing_memory_context')
        class Orchestrator:
            async def run(self,*args):
                token=marker.set('existing')
                try:
                    yield host._message('第一段',business=False)
                    await asyncio.sleep(0)
                    self.asserted=marker.get()
                    yield host._message('完整回答',final=True,business=False)
                finally:marker.reset(token)
        host.install_orchestrator(Orchestrator)
        self.internal.return_value={'attachment':None}
        e,b,c,s=fake_event(text='你好');o=Orchestrator()
        output=[x async for x in o.run(e,b,adapter_context=c)]
        self.assertEqual(o.asserted,'existing');self.assertEqual(output[-1].all_content,'完整回答')
    async def test_card_status_retains_review_and_knowledge_sources(self):
        state=CardState();state.apply({'type':'business.updated'});state.apply({'type':'payment.stage','stage':'payment_review'})
        state.apply({'type':'answer.snapshot','text':'请进入平台检查。'});state.apply({'type':'run.completed'})
        self.assertTrue(state.business);self.assertEqual(state.stage,'payment_review');self.assertEqual(state.sources,[])


if __name__=='__main__':unittest.main()