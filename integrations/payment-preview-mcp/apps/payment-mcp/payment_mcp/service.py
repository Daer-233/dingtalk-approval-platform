import asyncio
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

from .auth import PaymentError


def safe_text(value,limit=160):
    text=str(value or '')[:limit]
    text=re.sub(r'[A-Za-z]:[\\/][^\s]+','[平台文件]',text)
    text=re.sub(r'\b\d{9,}\b','[已隐藏]',text)
    return text


def amount(value):
    from decimal import Decimal, InvalidOperation
    try:
        number=Decimal(str(value if value is not None else 0))
        if not number.is_finite(): raise InvalidOperation()
        return format(number.quantize(Decimal('0.01')),'f')
    except (InvalidOperation,ValueError):
        raise PaymentError('invalid_amount','平台金额格式异常，请在平台核对。') from None


def serialized(function):
    @wraps(function)
    async def wrapped(self,*args,**kwargs):
        async with self.write_lock:
            return await function(self,*args,**kwargs)
    return wrapped


class PaymentService:
    def __init__(self,settings,store,backend):
        self.write_lock=asyncio.Lock()
        self.settings_provider=settings
        self.store=store
        self.backend=backend

    def gate(self,actor):
        cfg=self.settings_provider()
        if (not cfg.enabled or cfg.generation!=actor.generation or cfg.pilot_staff_id!=actor.staff or cfg.corp_id!=actor.corp
                or not self.store.run_active(actor)):
            raise PaymentError('unauthorized','付款工具已关闭或授权失效。',403)
        return cfg

    def link(self,task_id,actor):
        cfg=self.gate(actor)
        self.store.own(task_id,actor)
        return cfg.web_url.rstrip('/')+'/?task_id='+quote(task_id,safe='')

    def project(self,raw,actor):
        task_id=str(raw.get('id') or '')
        self.store.own(task_id,actor)
        analysis=raw.get('analysis') or {}
        results=raw.get('result') or {}
        groups=results.get('departments') or analysis.get('groups') or []
        summaries=[]
        for group in groups:
            summaries.append({
                'group_id':str(group.get('id') or group.get('group_id') or ''),
                'company':safe_text(group.get('company')),
                'payment_department':safe_text(group.get('payment_department') or group.get('suggested_department')),
                'count':int(group.get('count') or group.get('row_count') or 0),
                'amount':amount(group.get('amount',group.get('total_amount',0))),
                'known_rule':bool(group.get('known_rule',True)),
            })
        previews=[]
        needs=[]
        for p in raw.get('previews') or []:
            nodes=[]
            for n in p.get('nodes') or []:
                nodes.append({'name':safe_text(n.get('name')),'kind':safe_text(n.get('kind')),
                              'required':bool(n.get('required'))})
            previews.append({'group_id':str(p.get('group_id') or ''),'valid':p.get('valid') is True,
                'nodes':nodes,'missing':[safe_text(e) for e in p.get('errors') or []]})
            needs.extend(previews[-1]['missing'])
        if not raw.get('choices'):
            needs.append('付款部门尚未确认。')
        if any(not g['known_rule'] or not g['payment_department'] for g in summaries):
            needs.append('存在未识别部门，请在平台确认归属。')
        if not previews:
            needs.append('审批预览尚未完成；可能仍需填写付款资料或选择审批人。')
        state='ready_for_platform_confirmation' if previews and all(p['valid'] for p in previews) else 'needs_review'
        if raw.get('status')=='failed': state='needs_review'
        progress=raw.get('submission_progress') or {}
        return {'task_id':task_id,'status':state,'platform_status':safe_text(raw.get('status')),
                'file_name':safe_text(raw.get('file_name')),'groups':summaries,'previews':previews,
                'needs_review':list(dict.fromkeys(needs)),'review_url':self.link(task_id,actor),
                'submission_status':{k:int(v) for k,v in progress.items() if k in ('total','success','failed','uncertain','queued','submitting') and isinstance(v,int)},
                'notice':'本工具仅生成预览。请进入平台核对完整审批链，并由你确认提交。'}

    @serialized
    async def import_excel(self,attachment_ref,actor):
        self.gate(actor)
        attachment=self.store.attachment(attachment_ref,actor)
        key='import:'+attachment['ref']
        cached=self.store.begin(key,actor,'import')
        if cached is not None:
            return await self.get_task(cached['task_id'],actor) if cached.get('task_id') else cached
        try:
            path=Path(attachment['path'])
            if not path.is_file(): raise PaymentError('attachment_missing','附件暂不可用，请重新发送表格。',404)
            with path.open('rb') as handle:
                raw=await self.backend.request('POST',operation='import',files={'file':(attachment['name'],handle,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
            task_id=str(raw.get('id') or '')
            if not task_id:
                raise PaymentError('backend_uncertain','平台未返回任务编号，请先在平台核对。',503)
            # Ownership is recorded before returning anything to the model.
            self.store.add_task(task_id,actor)
            self.store.finish(key,{'task_id':task_id})
            return self.project(raw,actor)
        except BaseException as error:
            self.store.finish(key,uncertain=True)
            raise

    async def get_task(self,task_id,actor):
        self.gate(actor)
        self.store.own(task_id,actor)
        raw=await self.backend.request('GET',task_id)
        if str(raw.get('id'))!=task_id:
            raise PaymentError('task_mismatch','平台返回任务不一致，请核对。',409)
        return self.project(raw,actor)

    async def list_tasks(self,actor,limit=10):
        self.gate(actor)
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=30:
            raise PaymentError('invalid_limit','任务数量须为 1 至 30。')
        items=[]
        for task_id in self.store.task_ids(actor,limit):
            try: items.append(await self.get_task(task_id,actor))
            except PaymentError as error: items.append({'task_id':task_id,'status':error.code,'message':error.message})
        return {'tasks':items}

    @serialized
    async def prepare_preview(self,task_id,actor,choices=None):
        self.gate(actor)
        self.store.own(task_id,actor)
        raw=await self.backend.request('GET',task_id)
        if any(a.get('status') in ('success','uncertain','queued','submitting') for a in raw.get('approval_attempts') or []):
            result=self.project(raw,actor)
            result['needs_review'].append('任务已有审批提交记录，工具不会重新生成或修改，请在平台核对。')
            return result
        groups={str(g['id']):g for g in (raw.get('analysis') or {}).get('groups') or []}
        selected=choices or raw.get('choices')
        if not selected:
            # Only use deterministic rules, never invent an unmapped department.
            if not groups or any(not g.get('known_rule') or not g.get('suggested_department') for g in groups.values()):
                return self.project(raw,actor)
            selected=[{'group_id':gid,'selected':True,'payment_department':g['suggested_department']} for gid,g in groups.items()]
        cleaned=[]
        for choice in selected:
            if not isinstance(choice,dict) or set(choice)-{'group_id','selected','payment_department','remember_mapping','approvers'}:
                raise PaymentError('invalid_choices','部门选择无效，请在平台核对。')
            gid=str(choice.get('group_id') or '')
            if gid not in groups or any(c['group_id']==gid for c in cleaned):
                raise PaymentError('invalid_choices','付款分组不存在或重复。')
            department=str(choice.get('payment_department') or '').strip()
            if type(choice.get('selected',True)) is not bool or (choice.get('selected',True) and not department):
                raise PaymentError('invalid_choices','请选择正确的付款部门。')
            # No mutation of persistent mapping or approvers through this tool.
            cleaned.append({'group_id':gid,'selected':choice.get('selected',True),
                            'payment_department':department,'remember_mapping':False,'approvers':{}})
        if {c['group_id'] for c in cleaned}!=set(groups) or not any(c['selected'] for c in cleaned):
            raise PaymentError('invalid_choices','请明确每个分组是否处理，至少选择一个部门。')
        key='prepare:'+task_id+':'+hashlib.sha256(json.dumps(cleaned,sort_keys=True).encode()).hexdigest()
        cached=self.store.begin(key,actor,'prepare')
        if cached is not None:
            return await self.get_task(task_id,actor)
        try:
            if not raw.get('result'):
                await self.backend.request('PUT',task_id,'groups',json={'groups':cleaned})
                await self.backend.request('POST',task_id,'prepare')
            else:
                current=[{k:c.get(k) for k in ('group_id','selected','payment_department')} for c in raw.get('choices') or []]
                expected=[{k:c[k] for k in ('group_id','selected','payment_department')} for c in cleaned]
                if current!=expected:
                    raise PaymentError('review_required','任务已有拆分结果；更改部门请进入平台，避免覆盖人工调整。',409)
            await self.backend.request('POST',task_id,'forecast')
            await self.backend.request('POST',task_id,'preview')
            result=await self.get_task(task_id,actor)
            self.store.finish(key,{'task_id':task_id})
            return result
        except BaseException:
            self.store.finish(key,uncertain=True)
            raise
