import copy
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sdk_lab as lab


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Tests must never reach an external network.')
    monkeypatch.setattr(socket.socket, 'connect', blocked)


@pytest.fixture
def payload():
    return lab.load_request(lab.ROOT / 'fixtures/request.example.json')


def test_sdk_nested_models_and_wire_request(payload):
    client = lab.WorkflowClient(lab.client_config())
    client.execute = Mock(return_value={
        'statusCode': 200, 'headers': {}, 'body': {'instanceId': 'demo-instance'}})
    result = lab.create_once(payload, 'test-token', client)
    assert result['status'] == 'created'
    assert result['instance_id'] == 'demo-instance'
    assert client.execute.call_count == 1
    params, request, runtime = client.execute.call_args.args
    assert request.body == payload
    assert request.headers['x-acs-dingtalk-access-token'] == 'test-token'
    assert params.pathname == '/v1.0/workflow/processInstances'
    assert params.method == 'POST'
    assert client._protocol == 'https'
    assert runtime.autoretry is False and runtime.max_attempts == 1


def test_roundtrip_preserves_details_attachments_selection_and_omission(payload):
    original = copy.deepcopy(payload)
    assert lab.compare(payload)['equal']
    request = lab.build_request(payload)
    assert request.form_component_values[1].details[0].value == '12.50'
    mapped = request.to_map()
    assert mapped == original
    assert 'approvers' not in mapped and 'ccList' not in mapped
    assert json.loads(mapped['formComponentValues'][2]['value'])[0]['fileSize'] == '100'


def test_empty_optional_list_is_preserved(payload):
    payload['targetSelectActioners'] = []
    assert lab.compare(payload)['equal']


@pytest.mark.parametrize('field,value', [('deptId', '100'), ('deptId', True),
                                        ('microappAgentId', 1.5)])
def test_numeric_types_are_not_silently_changed(payload, field, value):
    payload[field] = value
    with pytest.raises(ValueError, match='integer'):
        lab.build_request(payload)


def test_unknown_fields_are_reported_instead_of_dropped(payload):
    payload['unknownParameter'] = 'demo'
    report = lab.compare(payload)
    assert not report['equal']
    assert report['differences'] == [{'path': '$.unknownParameter', 'reason': 'missing_or_added'}]


def test_raw_two_dimensional_details_are_rejected(payload):
    payload['formComponentValues'][1]['details'] = [[{'name': 'Amount', 'value': '12.50'}]]
    with pytest.raises(ValueError, match='object'):
        lab.build_request(payload)


class ApiError(Exception):
    code = 'processInstanceStartFailed'
    message = 'create failed'
    data = {'statusCode': 400, 'requestId': 'demo-trace'}


@pytest.mark.parametrize('error,status,code', [
    (ApiError(), 'failed', 'processInstanceStartFailed'),
    (TimeoutError('connection interrupted'), 'uncertain', None),
])
def test_failure_and_transport_ambiguity_never_retry(payload, error, status, code):
    client = lab.WorkflowClient(lab.client_config())
    client.execute = Mock(side_effect=error)
    result = lab.create_once(payload, 'test-token', client)
    assert client.execute.call_count == 1
    assert result['status'] == status and result['code'] == code
    assert result['retry_allowed'] is False
    if status == 'failed':
        assert result['http_status'] == 400 and result['request_id'] == 'demo-trace'


def test_success_without_instance_is_uncertain(payload):
    client = lab.WorkflowClient(lab.client_config())
    client.execute = Mock(return_value={'statusCode': 200, 'headers': {}, 'body': {}})
    assert lab.create_once(payload, 'test-token', client)['status'] == 'uncertain'
    assert client.execute.call_count == 1


def test_create_preview_never_loads_credentials_or_connects(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, 'load_credentials', Mock(side_effect=AssertionError('no credentials in preview')))
    output = tmp_path / 'preview.json'
    assert lab.main(['create', '--request', str(lab.ROOT / 'fixtures/request.example.json'),
                     '--output', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['status'] == 'preview_only' and report['network_calls'] == 0
    assert 'demo-user' not in output.read_text(encoding='utf-8')


def test_readonly_calls_schema_and_forecast_only(payload):
    client = lab.WorkflowClient(lab.client_config())
    def response(params, request, runtime):
        assert params.pathname in ('/v1.0/workflow/forms/schemas/processCodes',
                                   '/v1.0/workflow/processes/forecast')
        assert runtime.autoretry is False
        if params.pathname == '/v1.0/workflow/processes/forecast':
            assert request.body['userId'] == payload['originatorUserId']
            assert request.body['deptId'] == payload['deptId']
            assert request.body['processCode'] == payload['processCode']
            assert 'originatorUserId' not in request.body
            assert request.headers['x-acs-dingtalk-access-token'] == 'demo-token'
        return {'statusCode': 200, 'headers': {}, 'body': {'result': {'isForecastSuccess': True}}}
    client.execute = Mock(side_effect=response)
    report = lab.inspect_readonly(payload, {}, client=client, token='demo-token')
    assert client.execute.call_count == 2
    assert report['write_operations'] == 0 and report['forecast_success']


def test_token_uses_sdk_and_does_not_log_secret():
    client = lab.OAuthClient(lab.client_config())
    client.execute = Mock(return_value={'statusCode': 200, 'headers': {},
                                      'body': {'accessToken': 'demo-token', 'expireIn': 7200}})
    assert lab.access_token({'DINGTALK_CLIENT_ID': 'demo-id',
                             'DINGTALK_CLIENT_SECRET': 'REPLACE_ME'}, client) == 'demo-token'
    params, request, options = client.execute.call_args.args
    assert params.pathname == '/v1.0/oauth2/accessToken'
    assert request.body == {'appKey': 'demo-id', 'appSecret': 'REPLACE_ME'}
    assert client.execute.call_count == 1 and not options.autoretry


def test_credentials_only_from_explicit_file(monkeypatch, tmp_path):
    monkeypatch.setenv('DINGTALK_CLIENT_SECRET', 'ambient-secret')
    with pytest.raises(ValueError, match='explicit'):
        lab.load_credentials(None)
    env_file = tmp_path / '.env'
    env_file.write_text('DINGTALK_CLIENT_ID=demo\nDINGTALK_CLIENT_SECRET=local-secret\n', encoding='utf-8')
    assert lab.load_credentials(env_file)['DINGTALK_CLIENT_SECRET'] == 'local-secret'
    assert os.environ['DINGTALK_CLIENT_SECRET'] == 'ambient-secret'


def test_report_redacts_sensitive_values():
    value = {'accessToken': 'demo-token', 'message': 'secret-in-error https://example.invalid/private',
             'originatorUserId': 'demo-user', 'formComponentValues': [
                 {'name': 'Bank account', 'value': '1234567890123456'}]}
    text = json.dumps(lab.redact(value, ['secret-in-error']))
    assert 'demo-token' not in text and 'demo-user' not in text
    assert '1234567890123456' not in text and 'https://' not in text


def test_caches_are_local_and_c_is_rejected():
    paths = lab.configure_paths()
    assert all(Path(path).is_relative_to(lab.ROOT) for path in paths.values())
    assert all(Path(path).drive.lower() != 'c:' for path in paths.values())
    with pytest.raises(ValueError, match='other than C'):
        lab.configure_paths(Path('C:/sdk-test'))


def test_output_to_c_rejected_before_create(monkeypatch):
    monkeypatch.setattr(lab, 'load_credentials', Mock(side_effect=AssertionError('must stop before token')))
    monkeypatch.setattr(lab, 'save_report', Mock())
    code = lab.main(['create', '--execute', '--request',
                     str(lab.ROOT / 'fixtures/request.example.json'), '--output', 'C:/sdk-report.json'])
    assert code == 2
    lab.load_credentials.assert_not_called()


def test_nested_second_level_details_roundtrip(payload):
    payload['formComponentValues'][1]['details'][0]['details'] = [{'name': 'Nested', 'value': 'demo'}]
    assert lab.compare(payload)['equal']


def test_official_sdk_exception_keeps_status_and_trace(payload):
    from alibabacloud_tea_openapi.exceptions import ClientException
    error = ClientException(code='processInstanceInvalidParameter', message='parameter error',
                            status_code=400, request_id='sdk-demo-trace')
    client = lab.WorkflowClient(lab.client_config())
    client.execute = Mock(side_effect=error)
    record = lab.create_once(payload, 'demo-token', client)
    assert record['http_status'] == 400 and record['request_id'] == 'sdk-demo-trace'
    assert record['code'] == 'processInstanceInvalidParameter'
    assert record['status'] == 'failed' and client.execute.call_count == 1


def test_trace_id_from_sdk_error_message_is_preserved():
    error = Exception('code: 400, userId is mandatory. request id: 01A1335A-TEST-TRACE')
    assert lab.error_record(error)['request_id'] == '01A1335A-TEST-TRACE'
