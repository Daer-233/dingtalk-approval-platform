"""Independent, explicitly authorized single modern create with manual actors omitted."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.dingtalk import (API_BASE, OAPI_BASE, DingTalkClient,
    DingTalkUncertainSubmissionError, load_dingtalk_config, build_display_fields,
    resolve_display_fields_from_schema, form_values_from_schema, wire_form_values,
    forecast_signature)
from app.platform_dingtalk import normalize_forecast

BASE_OUT = ROOT / 'output' / 'no_manual_actioners_test_once_20261009'
OUT = BASE_OUT
TASK = '5632e043dc3a4a22aa7506875502f8f4'
GROUP = '961f83680a82ac'
PROCESS = 'PROC-DEMO-TEST-ONLY'
NAME = '副本 付款申请单11'
CREATE = API_BASE + '/v1.0/workflow/processInstances'
PROTECTED = ['.env', 'app/dingtalk.py', 'app/modern_chain.py', 'data/workflow.db']


def now():
    return datetime.now(timezone.utc).isoformat()


def write(name, data):
    temp = OUT / (name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(OUT / name)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROTECTED}


class SingleCreateTransport(httpx.BaseTransport):
    """Allow only this experiment's auth/read/upload endpoints and one create."""
    def __init__(self, report, submit):
        self.inner = httpx.HTTPTransport(retries=0)
        self.report = report
        self.submit = submit
        self.upload_urls = set()
        self.access_pages = []

    def handle_request(self, request):
        method, host, path = request.method, request.url.host, request.url.path
        is_create = method == 'POST' and str(request.url) == CREATE
        read_allowed = (
            host == 'api.dingtalk.com' and (method, path) in {
                ('POST', '/v1.0/oauth2/accessToken'),
                ('GET', '/v1.0/workflow/forms/schemas/processCodes'),
                ('POST', '/v1.0/workflow/processes/forecast'),
                ('POST', '/v1.0/workflow/processInstances/spaces/infos/query'),
            }
        ) or (host == 'oapi.dingtalk.com' and method == 'POST' and path in {
            '/topapi/process/listbyuserid', '/topapi/v2/user/get', '/topapi/processinstance/get',
        })
        upload_allowed = self.submit and (OUT / 'create_once_guard.json').exists() and (
            (host == 'api.dingtalk.com' and method == 'POST' and
             path.startswith('/v1.0/storage/spaces/') and
             (path.endswith('/files/uploadInfos/query') or path.endswith('/files/commit')))
            or (method == 'PUT' and str(request.url) in self.upload_urls)
        )
        if is_create:
            if not self.submit or not (OUT / 'create_once_guard.json').exists() or self.report['create_calls']:
                raise RuntimeError('一次创建限制已触发；未发送。')
            payload = json.loads(request.content)
            if set(payload) != {'originatorUserId', 'processCode', 'deptId', 'microappAgentId', 'formComponentValues'}:
                raise RuntimeError('创建参数超出授权范围；未发送。')
            self.report.update(create_calls=1, status='creating', create_started_at=now())
            write('report.json', self.report)  # Persist consumption before any network I/O.
            write('sent_request.json', payload)
        elif not (read_allowed or upload_allowed):
            raise RuntimeError('测试程序阻止了授权范围之外的网络调用。')
        response = self.inner.handle_request(request)
        response.read()
        if path == '/topapi/process/listbyuserid':
            self.access_pages.append(response.json())
            write('access_pages.json', self.access_pages)
        if is_create:
            try:
                body = response.json()
            except ValueError:
                body = {'raw_body': response.text}
            trace = (body.get('requestId') or body.get('request_id') or body.get('requestid')
                     or response.headers.get('x-acs-request-id') or response.headers.get('x-request-id'))
            self.report.update(http_status=response.status_code, trace_id=trace,
                               error_code=body.get('code'))
            write('create_response.json', {'http_status': response.status_code,
                  'body': body, 'trace_id': trace, 'headers': dict(response.headers)})
        if upload_allowed and path.endswith('/files/uploadInfos/query') and not response.is_error:
            body = response.json()
            info = body.get('result', body)
            urls = (info.get('headerSignatureInfo') or {}).get('resourceUrls') or []
            if any(httpx.URL(url).scheme != 'https' for url in urls):
                raise RuntimeError('附件地址不是 HTTPS，停止测试。')
            self.upload_urls.update(urls)
        return response

    def close(self):
        self.inner.close()


def run(submit=False):
    OUT.mkdir(exist_ok=True)
    guard = OUT / 'create_once_guard.json'
    if guard.exists():
        raise RuntimeError('这次测试已经执行或进入提交阶段；禁止重跑。请查看已保存结果。')
    before = hashes()
    cfg = load_dingtalk_config()
    if OUT != BASE_OUT and cfg.originator_user_id != '999999999':
        raise RuntimeError('发起人不是用户刚修改的账号，未开展继续测试。')
    if cfg.process_code != PROCESS or not cfg.attachment_enabled:
        raise RuntimeError('模板或附件配置与已授权计划不一致，未开展测试。')
    db = sqlite3.connect((ROOT / 'data/workflow.db').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("select count(*) from approval_attempts where status in ('queued','submitting')").fetchone()[0]:
            raise RuntimeError('存在其他提交任务，未开展测试。')
        record = db.execute('select status,data_json from tasks where id=?', (TASK,)).fetchone()
        if not record or record['status'] != 'prepared':
            raise RuntimeError('测试业务任务状态发生变化。')
        if db.execute('select count(*) from approval_attempts where task_id=?', (TASK,)).fetchone()[0]:
            raise RuntimeError('该业务任务已经有提交记录，未开展测试。')
        task = json.loads(record['data_json'])
        group = next(g for g in task['result']['departments'] if g['id'] == GROUP)
        profile = dict(db.execute('select * from company_profiles where company=?', (group['company'],)).fetchone())
    finally:
        db.close()
    attachment_path = (ROOT / group['file_path']).resolve()
    if Decimal(str(group['amount'])) != Decimal('1200') or group['payment_department'] != '总经办':
        raise RuntimeError('测试金额或部门与授权不一致。')
    if not attachment_path.is_file() or attachment_path.name != '总经办7月补贴明细.xlsx':
        raise RuntimeError('原附件不存在或文件名变化。')
    if any(not profile.get(key) for key in ('payment_entity', 'supplier_name', 'bank_account', 'bank_name')):
        raise RuntimeError('付款基础资料不完整。')
    report = {'started_at': now(), 'local_test_id': uuid.uuid4().hex,
              'task_id': TASK, 'group_id': GROUP, 'process_code': PROCESS,
              'originator_user_id': cfg.originator_user_id,
              'application_live_submit_enabled': cfg.live_submit_enabled,
              'template_name': NAME, 'amount': '1200', 'department': '总经办',
              'variant': 'omit_all_manual_approver_and_cc_parameters',
              'create_calls': 0, 'attachment_upload_calls': 0, 'status': 'preparing',
              'authorization': 'REPLACE_ME',
              'authorization_scope': 'REPLACE_ME' if OUT.name.endswith('_user_repeat_2') else 'Original single-attempt authorization.',
              'attachment_sha256': hashlib.sha256(attachment_path.read_bytes()).hexdigest(),
              'steps': []}

    def stage(message):
        report['steps'].append({'name': message, 'at': now()})
        write('report.json' if submit else 'preparation_report.json', report)
        print(message, flush=True)

    def forecast(values, filename):
        raw = client.forecast(values)
        normalized = normalize_forecast(raw)
        write(filename, {'raw': raw, 'normalized': normalized})
        if not normalized.get('success'):
            raise RuntimeError('流程预测未通过，未创建。')
        return normalized

    client = DingTalkClient(replace(cfg, live_submit_enabled=False))
    client.close()
    client._http = httpx.Client(transport=SingleCreateTransport(report, submit), timeout=30, follow_redirects=False)
    try:
        schema = client.get_schema(force_refresh=True)
        write('schema.json', schema)
        if schema.get('name') != NAME or schema.get('status') != 'PUBLISHED' or schema.get('formCode') != PROCESS:
            raise RuntimeError('模板名称、发布状态或编码变化，未创建。')
        access = client.originator_process_access()
        write('access.json', access)
        report['originator_access_list_check'] = access
        if not access.get('allowed'):
            report['preflight_warning'] = '可发起列表未包含当前模板；保留检查结果，由唯一一次创建请求返回实际权限/创建结果。'
        stage('模板发布状态检查通过；发起权限结果已保存')
        fields = resolve_display_fields_from_schema(schema, build_display_fields(
            group, profile, task['result']['month'], attachment_path.name, cfg.effective_applicant_dept_id))
        values = form_values_from_schema(schema, fields, include_calculated=False)
        first = forecast(values, 'forecast_before_upload.json')
        report['manual_nodes'] = [n for n in first['nodes'] if n.get('kind') == 'manual']
        stage('完整业务表单的流程预测通过；不提供自选人员')
        if not submit:
            report['status'] = 'ready_not_submitted'
        else:
            with guard.open('x', encoding='utf-8') as stream:
                json.dump({'test_id': report['local_test_id'], 'started_at': now(),
                           'max_create_calls': 1, 'process_code': PROCESS}, stream)
            report['attachment_upload_calls'] = 1
            stage('上传原有1200元部门附件')
            attachment = client.upload_approval_attachment(attachment_path)
            write('attachment.json', attachment)
            values = form_values_from_schema(schema, fields, attachment, include_calculated=False)
            latest = forecast(values, 'forecast_with_attachment.json')
            if forecast_signature(first['nodes']) != forecast_signature(latest['nodes']):
                raise RuntimeError('附件上传后流程节点发生变化，未创建。')
            payload = {'originatorUserId': cfg.originator_user_id, 'processCode': PROCESS,
                       'deptId': -1 if int(cfg.originator_dept_id) == 1 else int(cfg.originator_dept_id),
                       'microappAgentId': int(cfg.agent_id),
                       'formComponentValues': wire_form_values(values, include_details=True)}
            wired = {field['name']: field for field in payload['formComponentValues']}
            if json.loads(wired['附件']['value']) != [attachment]:
                raise RuntimeError('附件未正确保留，未创建。')
            total = sum((Decimal(cell['value']) for name in ('项目分摊明细', '部门分摊明细')
                         for row in json.loads(wired[name]['value']) for cell in row
                         if cell['name'] == '金额（元）'), Decimal(0))
            if total != Decimal('1200') or before != hashes():
                raise RuntimeError('明细金额或受保护文件在测试过程中变化，未创建。')
            write('candidate_request.json', payload)
            stage('附件及流程复核通过；现在仅发送一次创建请求')
            response = client._request('POST', CREATE, json=payload, retries=0, uncertain_on_transport=True)
            instance_id = response.get('instanceId') or (response.get('result') or {}).get('instanceId')
            if not instance_id:
                raise DingTalkUncertainSubmissionError('创建响应未返回实例ID；先核对审批列表，禁止重复创建。')
            report.update(status='created', instance_id=str(instance_id))
            stage('已返回审批实例ID；只读核对实际流程')
            try:
                detail = client._request('POST', OAPI_BASE + '/topapi/processinstance/get',
                    params={'access_token': client.access_token()}, json={'process_instance_id': str(instance_id)}, retries=0)
                write('created_instance_detail.json', detail)
                instance = detail.get('process_instance') or detail.get('result') or detail
                report['instance_status'] = instance.get('status')
                report['business_id'] = instance.get('business_id')
                report['actual_tasks'] = instance.get('tasks')
                report['manual_completion_verified'] = False
            except Exception as exc:
                report['detail_query_error'] = str(exc)
    except DingTalkUncertainSubmissionError as exc:
        report.update(status='uncertain', error=str(exc), must_reconcile_before_any_retry=True)
    except Exception as exc:
        report.update(status='failed' if report['create_calls'] else 'stopped_before_create', error=str(exc))
    finally:
        client.close()
        after = hashes()
        report.update(finished_at=now(), protected_unchanged=before == after,
                      protected_hashes_before=before, protected_hashes_after=after)
        write('report.json' if submit else 'preparation_report.json', report)
    return {key: value for key, value in report.items() if key not in {'manual_nodes', 'protected_hashes_before', 'protected_hashes_after', 'actual_tasks'}}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--submit-once', action='store_true')
    parser.add_argument('--resume-after-initiator-change', action='store_true')
    parser.add_argument('--user-authorized-repeat', action='store_true')
    args = parser.parse_args()
    if args.resume_after_initiator_change and args.user_authorized_repeat:
        raise RuntimeError('不能同时使用继续和重新发起选项。')
    if args.resume_after_initiator_change:
        previous = json.loads((BASE_OUT / 'report.json').read_text(encoding='utf-8'))
        if (previous.get('create_calls') != 0 or previous.get('status') != 'stopped_before_create'
                or not previous.get('finished_at') or (BASE_OUT / 'sent_request.json').exists()
                or (BASE_OUT / 'create_response.json').exists()):
            raise RuntimeError('此前测试已发送或结果不明，禁止继续创建。')
        OUT = ROOT / 'output' / 'no_manual_actioners_test_once_20261009_initiator_changed'
        PROTECTED.extend(['output/no_manual_actioners_test_once_20261009/report.json',
                          'output/no_manual_actioners_test_once_20261009/create_once_guard.json'])
    if args.user_authorized_repeat:
        previous_dir = ROOT / 'output' / 'no_manual_actioners_test_once_20261009_initiator_changed'
        previous = json.loads((previous_dir / 'report.json').read_text(encoding='utf-8'))
        response = json.loads((previous_dir / 'create_response.json').read_text(encoding='utf-8'))
        if (previous.get('status') != 'failed' or previous.get('create_calls') != 1
                or previous.get('http_status') != 400 or response.get('http_status') != 400
                or not previous.get('finished_at') or previous.get('instance_id')):
            raise RuntimeError('上一轮不是已确认的400失败，禁止重新创建。')
        OUT = ROOT / 'output' / 'no_manual_actioners_test_once_20261009_user_repeat_2'
        for filename in ('report.json', 'create_response.json', 'sent_request.json', 'create_once_guard.json'):
            PROTECTED.append(str((previous_dir / filename).relative_to(ROOT)))
    print(json.dumps(run(args.submit_once), ensure_ascii=False, indent=2))
