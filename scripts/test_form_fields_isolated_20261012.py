"""Two explicitly requested, single-variable form experiments. Stop on first creation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import test_no_manual_actioners_once as guarded
from app.dingtalk import (DingTalkClient, DingTalkUncertainSubmissionError,
    load_dingtalk_config, build_display_fields, resolve_display_fields_from_schema,
    form_values_from_schema, wire_form_values, forecast_signature, OAPI_BASE)
from app.platform_dingtalk import normalize_forecast

OUT = ROOT / 'output' / 'form_fields_isolated_20261012'
CALCULATED = '付款金额含税（元）'
HIDDEN = '其他公司'
PREVIOUS = ROOT / 'output' / 'no_manual_actioners_test_once_20261009_user_repeat_2'
PROTECTED = ['.env', 'app/dingtalk.py', 'app/modern_chain.py', 'data/workflow.db',
             'scripts/test_no_manual_actioners_once.py']
PROTECTED += [str(path.relative_to(ROOT)) for path in PREVIOUS.glob('*.json')]


def now():
    return datetime.now(timezone.utc).isoformat()


def write(name, value, directory=OUT):
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / (name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(directory / name)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROTECTED}


class UploadsAndReadsOnly(guarded.SingleCreateTransport):
    def handle_request(self, request):
        if request.method == 'POST' and str(request.url) == guarded.CREATE:
            raise RuntimeError('准备阶段禁止创建审批。')
        return super().handle_request(request)


def payload(cfg, values):
    return {'originatorUserId': cfg.originator_user_id, 'processCode': cfg.process_code,
            'deptId': -1 if int(cfg.originator_dept_id) == 1 else int(cfg.originator_dept_id),
            'microappAgentId': int(cfg.agent_id),
            'formComponentValues': wire_form_values(values, include_details=True)}


def one_field_difference(base, candidate, field_name, field_value):
    before = {v['name']: v for v in base['formComponentValues']}
    after = {v['name']: v for v in candidate['formComponentValues']}
    if len(before) != len(base['formComponentValues']) or len(after) != len(candidate['formComponentValues']):
        raise RuntimeError('发现重复表单字段。')
    if set(after) - set(before) != {field_name} or set(before) - set(after):
        raise RuntimeError('实验变化超出一个新增字段。')
    if any(after[key] != value for key, value in before.items()):
        raise RuntimeError('其他表单值发生变化。')
    if after[field_name] != {'name': field_name, 'value': field_value}:
        raise RuntimeError('实验字段值与授权不符。')
    if {k: v for k, v in base.items() if k != 'formComponentValues'} != {
            k: v for k, v in candidate.items() if k != 'formComponentValues'}:
        raise RuntimeError('表单之外的创建参数发生变化。')
    return {'added': after[field_name], 'other_fields_unchanged': True,
            'other_request_parameters_unchanged': True}


def run(submit):
    OUT.mkdir(exist_ok=True)
    bundle_guard = OUT / 'experiment_once_guard.json'
    if bundle_guard.exists():
        raise RuntimeError('本轮实验已经进入执行阶段；请读取记录，禁止重复执行。')
    before = hashes()
    report = {'started_at': now(), 'status': 'preparing', 'create_calls': 0,
              'authorization': 'REPLACE_ME',
              'amount': '1200', 'template_name': guarded.NAME, 'experiments': [],
              'limits': 'A: calculated=1200 only; B: hidden empty string only. Each at most once. Stop on success or uncertainty.',
              'limitation': 'Mandatory manual approvers remain omitted as in baseline; failure cannot exclude a field issue.'}
    cfg = load_dingtalk_config()
    if cfg.process_code != guarded.PROCESS or cfg.originator_user_id != '999999999' or not cfg.attachment_enabled:
        raise RuntimeError('当前模板、发起人或附件开关与本次实验基准不同。')
    db = sqlite3.connect((ROOT / 'data/workflow.db').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        record = db.execute('select status,data_json from tasks where id=?', (guarded.TASK,)).fetchone()
        if not record or record['status'] != 'prepared':
            raise RuntimeError('基准任务状态变化。')
        if db.execute('select count(*) from approval_attempts where task_id=?', (guarded.TASK,)).fetchone()[0]:
            raise RuntimeError('基准任务已出现提交记录。')
        if db.execute("select count(*) from approval_attempts where status in ('queued','submitting')").fetchone()[0]:
            raise RuntimeError('存在其他正在提交的业务任务。')
        task = json.loads(record['data_json'])
        group = next(g for g in task['result']['departments'] if g['id'] == guarded.GROUP)
        profile = dict(db.execute('select * from company_profiles where company=?', (group['company'],)).fetchone())
    finally:
        db.close()
    file_path = Path(group['file_path']).resolve()
    if Decimal(str(group['amount'])) != Decimal('1200') or group['payment_department'] != '总经办' or not file_path.is_file():
        raise RuntimeError('金额、部门或附件与基准不同。')
    prior_report = json.loads((PREVIOUS / 'report.json').read_text(encoding='utf-8'))
    if hashlib.sha256(file_path.read_bytes()).hexdigest() != prior_report['attachment_sha256']:
        raise RuntimeError('附件内容与基准不同。')
    report.update(originator_user_id=cfg.originator_user_id, process_code=cfg.process_code,
                  attachment_sha256=prior_report['attachment_sha256'])
    guarded.OUT = OUT
    prepare_report = {'create_calls': 0}
    client = DingTalkClient(replace(cfg, live_submit_enabled=False))
    client.close()
    client._http = httpx.Client(transport=UploadsAndReadsOnly(prepare_report, submit), timeout=30, follow_redirects=False)

    def stage(message):
        report['stage'] = message
        write('report.json', report)
        print(message, flush=True)

    def forecast(values, filename):
        raw = client.forecast(values)
        normalized = normalize_forecast(raw)
        write(filename, {'raw': raw, 'normalized': normalized})
        if not normalized.get('success'):
            raise RuntimeError('实验流程预测未通过。')
        return normalized

    try:
        schema = client.get_schema(force_refresh=True)
        write('schema.json', schema)
        if (schema.get('name') != guarded.NAME or schema.get('status') != 'PUBLISHED'
                or schema.get('formCode') != guarded.PROCESS):
            raise RuntimeError('专用模板名称、编码或发布状态变化。')
        access = client.originator_process_access()
        report['access'] = access
        if not access.get('allowed'):
            raise RuntimeError('发起权限检查未通过。')
        items = DingTalkClient._schema_items(schema)
        definitions = {DingTalkClient._label(item): item for item in items}
        if definitions[CALCULATED].get('componentName') != 'CalculateField' or definitions[HIDDEN].get('componentName') != 'TextField':
            raise RuntimeError('实验字段类型变化。')
        fields = resolve_display_fields_from_schema(schema, build_display_fields(
            group, profile, task['result']['month'], file_path.name, cfg.effective_applicant_dept_id))
        initial_values = form_values_from_schema(schema, fields, include_calculated=False)
        initial = forecast(initial_values, 'forecast_baseline_before_upload.json')
        stage('模板、权限及基准流程预测通过')
        if not submit:
            report['status'] = 'prepared_without_upload_or_create'
            return report
        with bundle_guard.open('x', encoding='utf-8') as stream:
            json.dump({'started_at': now(), 'maximum_create_calls': 2,
                       'stop_on_success_or_uncertainty': True}, stream)
        with (OUT / 'create_once_guard.json').open('x', encoding='utf-8') as stream:
            json.dump({'purpose': 'attachment upload only; prepare transport blocks all creates'}, stream)
        stage('上传本轮共用附件；两个实验使用同一附件信息')
        attachment = client.upload_approval_attachment(file_path)
        write('attachment.json', attachment)
        values = form_values_from_schema(schema, fields, attachment, include_calculated=False)
        base = payload(cfg, values)
        write('baseline_request.json', base)
        prior_request = json.loads((PREVIOUS / 'sent_request.json').read_text(encoding='utf-8'))
        def without_attachment(request):
            copy = deepcopy(request)
            copy['formComponentValues'] = [v for v in copy['formComponentValues'] if v['name'] != '附件']
            return copy
        if without_attachment(base) != without_attachment(prior_request):
            raise RuntimeError('当前基准请求除新上传附件外与上次不同，停止创建。')
        latest = forecast(values, 'forecast_baseline_with_attachment.json')
        signature = forecast_signature(latest['nodes'])
        if signature != forecast_signature(initial['nodes']):
            raise RuntimeError('上传附件后基准流程变化。')
        report['baseline_nodes'] = latest['nodes']
        a_values = form_values_from_schema(schema, fields, attachment, include_calculated=True)
        b_values = deepcopy(values)
        hidden_item = {'name': HIDDEN, 'value': '', 'componentType': 'TextField'}
        position = {DingTalkClient._label(item): i for i, item in enumerate(items)}
        insertion = next((i for i, v in enumerate(b_values) if position[v['name']] > position[HIDDEN]), len(b_values))
        b_values.insert(insertion, hidden_item)
        for experiment, name, value, candidate_values in [
                ('A_calculated_only', CALCULATED, '1200', a_values),
                ('B_hidden_only', HIDDEN, '', b_values)]:
            candidate = payload(cfg, candidate_values)
            difference = one_field_difference(base, candidate, name, value)
            write(experiment + '_candidate.json', candidate)
            write(experiment + '_difference.json', difference)
        for experiment, name, value, candidate_values in [
                ('A_calculated_only', CALCULATED, '1200', a_values),
                ('B_hidden_only', HIDDEN, '', b_values)]:
            candidate = payload(cfg, candidate_values)
            predicted = forecast(candidate_values, experiment + '_forecast.json')
            if forecast_signature(predicted['nodes']) != signature:
                raise RuntimeError('实验表单改变了审批路径，停止创建。')
            if hashes() != before or hashlib.sha256(file_path.read_bytes()).hexdigest() != prior_report['attachment_sha256']:
                raise RuntimeError('配置、业务资料或原附件在执行中变化，停止创建。')
            phase_dir = OUT / experiment
            phase_dir.mkdir(exist_ok=True)
            phase = {'experiment': experiment, 'changed_field': name, 'changed_value': value,
                     'create_calls': 0, 'status': 'ready', 'started_at': now()}
            with (phase_dir / 'create_once_guard.json').open('x', encoding='utf-8') as stream:
                json.dump({'maximum_create_calls': 1, 'experiment': experiment}, stream)
            client._http.close()
            guarded.OUT = phase_dir
            client._http = httpx.Client(transport=guarded.SingleCreateTransport(phase, True), timeout=30, follow_redirects=False)
            stage('实验' + experiment + '：单变量校验通过，发送唯一一次创建请求')
            try:
                result = client._request('POST', guarded.CREATE, json=candidate, retries=0, uncertain_on_transport=True)
                instance_id = result.get('instanceId') or (result.get('result') or {}).get('instanceId')
                if not instance_id:
                    raise DingTalkUncertainSubmissionError('未返回实例ID；停止后续实验，先核对审批列表。')
                phase.update(status='created', instance_id=str(instance_id))
                try:
                    detail = client._request('POST', OAPI_BASE + '/topapi/processinstance/get', token=False,
                        params={'access_token': client.access_token()}, json={'process_instance_id': str(instance_id)}, retries=0)
                    write('created_instance_detail.json', detail, phase_dir)
                    actual = detail.get('process_instance') or detail.get('result') or detail
                    phase.update(instance_status=actual.get('status'), business_id=actual.get('business_id'),
                                 tasks=actual.get('tasks'), manual_completion_verified=False)
                except Exception as exc:
                    phase['detail_query_error'] = str(exc)
            except DingTalkUncertainSubmissionError as exc:
                phase.update(status='uncertain', error=str(exc))
            except Exception as exc:
                phase.update(status='failed' if phase.get('http_status') == 400 else 'uncertain', error=str(exc))
            finally:
                phase['finished_at'] = now()
                write('report.json', phase, phase_dir)
                report['experiments'].append(phase)
                report['create_calls'] += phase['create_calls']
                write('report.json', report)
            if phase['status'] != 'failed':
                report['status'] = 'stopped_after_' + phase['status']
                break
        else:
            report['status'] = 'both_experiments_failed'
    except Exception as exc:
        report.update(status='stopped', error=str(exc))
    finally:
        client.close()
        report.update(finished_at=now(), protected_unchanged=before == hashes(),
                      protected_hashes_before=before, protected_hashes_after=hashes())
        write('report.json', report)
    return {k: v for k, v in report.items() if k not in {'baseline_nodes', 'protected_hashes_before', 'protected_hashes_after'}}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--submit-experiments', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.submit_experiments), ensure_ascii=False, indent=2))
