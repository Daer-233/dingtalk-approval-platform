from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / 'instances/payments/config.private.json'
TOOLS = ('payment_import_excel', 'payment_get_task', 'payment_prepare_preview',
         'payment_list_tasks', 'payment_get_review_link')


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    generation: int = 0
    pilot_staff_id: str = ''
    corp_id: str = ''
    bot_id: str = '9777ca2e-7df1-4441-84ca-abedd4bc5c01'
    processor_ids: tuple = ()
    host_secret: str = ''
    backend_url: str = 'http://127.0.0.1:8000'
    web_url: str = ''
    data_root: Path = ROOT / 'data/payments'
    max_bytes: int = 20 * 1024 * 1024
    backend_timeout: float = 120

    @classmethod
    def load(cls, path=DEFAULT_CONFIG):
        p = Path(path)
        values = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        allowed = set(cls.__dataclass_fields__)
        if set(values) - allowed:
            raise ValueError('Unknown payment configuration field')
        if 'data_root' in values:
            values['data_root'] = Path(values['data_root'])
        if 'processor_ids' in values:
            values['processor_ids'] = tuple(values['processor_ids'])
        result = cls(**values)
        if type(result.generation) is not int or result.generation < 0:
            raise ValueError('Invalid payment authorization generation')
        if type(result.enabled) is not bool:
            raise ValueError('Payment enabled must be a boolean')
        if result.enabled and (not result.pilot_staff_id or not result.corp_id
                or not result.processor_ids or len(result.host_secret) < 32 or not result.web_url):
            raise ValueError('Payment pilot identity and credentials must be configured before enabling')
        for value in (result.backend_url, result.web_url):
            if value:
                parsed = urlsplit(value)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('Payment base URL is invalid')
        if urlsplit(result.backend_url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Payment backend must be the existing local platform')
        return result
