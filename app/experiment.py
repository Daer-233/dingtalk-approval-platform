"""Diagnostic application boundary. The original modern client stays unchanged."""
from dataclasses import replace
import httpx
import threading
from .dingtalk import DingTalkError
from .platform_dingtalk import PlatformDingTalkClient

READ_ROUTES = {
    "api.dingtalk.com": {
        ("POST", "/v1.0/oauth2/accessToken"),
        ("GET", "/v1.0/workflow/forms/schemas/processCodes"),
        ("POST", "/v1.0/workflow/processes/forecast"),
        ("POST", "/v1.0/workflow/processInstances/spaces/infos/query"),
    },
    "oapi.dingtalk.com": {
        ("POST", "/topapi/process/listbyuserid"),
        ("POST", "/topapi/v2/user/get"),
        ("POST", "/topapi/v2/user/list"),
        ("POST", "/topapi/v2/department/listsubid"),
    },
}
BLOCK_MESSAGE = "审批诊断实验版只读：已在发送前阻止附件上传、创建审批及审批操作。"

def require_read_request(method, url):
    url = httpx.URL(url)
    if (url.scheme != "https" or url.port not in (None, 443) or url.userinfo
            or (method.upper(), url.path) not in READ_ROUTES.get(url.host, set())):
        raise DingTalkError(BLOCK_MESSAGE)

class DiagnosticTransport(httpx.BaseTransport):
    def __init__(self, inner=None):
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        require_read_request(request.method, request.url)
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()

class DiagnosticDingTalkClient(PlatformDingTalkClient):
    def __init__(self, config=None, *, transport=None):
        super().__init__(config)
        self._diagnostic_local = threading.local()
        self.last_failure = None
        self._http.close()
        self._http = httpx.Client(transport=DiagnosticTransport(transport), timeout=30,
                                  follow_redirects=False, event_hooks={"response": [self._capture_response]})
        self._readonly_config()

    @property
    def last_failure(self):
        return getattr(self._diagnostic_local, "failure", None)

    @last_failure.setter
    def last_failure(self, value):
        self._diagnostic_local.failure = value

    def _capture_response(self, response):
        response.read()
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and (response.is_error or body.get("code") not in (None, "", "0", 0) or body.get("errcode") not in (None, 0)):
            self.last_failure = {"http_status": response.status_code,
                "body": {k: body[k] for k in ("code", "errcode", "message", "errmsg", "requestid", "requestId", "request_id", "instanceId") if k in body},
                "trace_id": response.headers.get("x-acs-request-id") or response.headers.get("x-request-id"),
                "stage": response.request.url.path}

    def _readonly_config(self):
        self.config = replace(self.config, live_submit_enabled=False, attachment_enabled=False)

    def reload_config(self):
        result = super().reload_config()
        self._readonly_config()
        return result

    def _request(self, method, url, **kwargs):
        # Check before acquiring a token, and again at transport for direct HTTP calls.
        require_read_request(method, url)
        self.last_failure = None
        return super()._request(method, url, **kwargs)

    def public_config(self):
        return {**super().public_config(), "read_only": True,
                "edition": "审批诊断实验版", "live_submit_enabled": False,
                "attachment_enabled": False}

    def start_process(self, *args, **kwargs):
        raise DingTalkError(BLOCK_MESSAGE)

    def upload_approval_attachment(self, *args, **kwargs):
        raise DingTalkError(BLOCK_MESSAGE)
