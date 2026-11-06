import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time


class PaymentError(Exception):
    def __init__(self, code, message, http_status=400):
        self.code, self.message, self.http_status = code, message, http_status
        super().__init__(message)


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def sign_claims(claims, secret):
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(',', ':')).encode())
    signature = _b64(hmac.new(secret.encode(), ('v1.' + payload).encode(), hashlib.sha256).digest())
    return 'v1.' + payload + '.' + signature


@dataclass(frozen=True)
class Actor:
    staff: str
    corp: str
    workspace: str
    event: str
    grant: str
    expires: int
    generation: int = 0


def verify(header, config, store, *, registering=False):
    if not config.enabled:
        raise PaymentError('disabled', '付款工具尚未启用。', 403)
    try:
        prefix, token = header.split(' ', 1)
        version, payload, signature = token.split('.')
        if prefix != 'Bearer' or version != 'v1' or len(token) > 8192:
            raise ValueError()
        expected = _b64(hmac.new(config.host_secret.encode(), ('v1.' + payload).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        now = int(time.time())
        if (claims.get('generation') != config.generation or claims.get('iss') != 'work-knowledge-payment-host' or claims.get('target_type') != 'person'
                or claims.get('staff') != config.pilot_staff_id or claims.get('corp') != config.corp_id
                or claims.get('bot') != config.bot_id or claims.get('processor') not in config.processor_ids
                or not isinstance(claims.get('iat'), int) or not isinstance(claims.get('exp'), int)
                or claims['iat'] > now + 15 or claims['exp'] <= now or claims['exp'] - claims['iat'] > 1800):
            raise ValueError()
        for key in ('workspace', 'event', 'grant'):
            if not isinstance(claims.get(key), str) or not 1 <= len(claims[key]) <= 256:
                raise ValueError()
        actor = Actor(claims['staff'], claims['corp'], claims['workspace'], claims['event'], claims['grant'], claims['exp'], config.generation)
        if not registering and not store.run_active(actor):
            raise ValueError()
        return actor
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise PaymentError('unauthorized', '付款工具授权已失效或无权访问。', 403) from None
