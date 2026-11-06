import httpx
from urllib.parse import quote

from .auth import PaymentError


class Backend:
    """An explicit HTTP allowlist. Approval submission is not representable here."""
    def __init__(self, settings, client=None):
        self.client = client or httpx.AsyncClient(base_url=settings.backend_url,
            timeout=settings.backend_timeout, trust_env=False, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, method, task_id=None, operation='', **kwargs):
        if task_id is None:
            if (method, operation) != ('POST', 'import'):
                raise PaymentError('forbidden_operation', '不支持的付款操作。', 403)
            path = '/api/tasks'
        else:
            if not isinstance(task_id, str) or not task_id or len(task_id)>80 or any(c not in '0123456789abcdefABCDEF-' for c in task_id):
                raise PaymentError('invalid_task', '任务编号无效。')
            allowed = {('GET',''): '', ('PUT','groups'):'/groups', ('POST','prepare'):'/prepare',
                       ('POST','forecast'):'/approvals/forecast', ('POST','preview'):'/approvals/preview'}
            if (method,operation) not in allowed:
                raise PaymentError('forbidden_operation', '工具不能提交审批或修改付款资料。', 403)
            path='/api/tasks/'+quote(task_id,safe='')+allowed[(method,operation)]
        try:
            response=await self.client.request(method,path,**kwargs)
        except (httpx.TimeoutException,httpx.NetworkError):
            raise PaymentError('backend_uncertain' if method!='GET' else 'backend_unavailable',
                '平台响应中断，处理结果需要核对；不会自动重发。' if method!='GET' else '审批平台暂时不可用，请稍后查询。',503) from None
        if not response.is_success:
            # Do not return backend exception details, paths or personal cell values.
            raise PaymentError('platform_rejected', '平台未完成处理，请在平台检查表格、付款资料及任务状态。',409 if response.status_code<500 else 503)
        try:
            value=response.json()
            if not isinstance(value,dict): raise ValueError()
            return value
        except ValueError:
            raise PaymentError('backend_uncertain','平台返回结果无法确认，请先在平台核对任务。',503) from None
