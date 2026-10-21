"""Short-lived, action-bound owner approvals, signed by a local 0600 operator key."""
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from pathlib import Path

from .accounting import units


class ApprovalError(ValueError):
    pass


def operator_key(database):
    path = Path(str(database)+'.operator-key')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as output:
            output.write(secrets.token_hex(32))
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ApprovalError('Operator key must be a regular owner-only (0600) file')
    return bytes.fromhex(path.read_text().strip())


def encode(payload):
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')


def issue(key, action, amount=None, lifetime=120, now=None, subject=None):
    if action not in ('release', 'approve', 'live-order', 'live-cancel','session-start'):
        raise ApprovalError('Unsupported approval action')
    now = int(time.time() if now is None else now)
    payload = dict(action=action, actor=getpass.getuser(), nonce=secrets.token_hex(16),
                   expires=now+lifetime, issued=now)
    if action == 'release':
        if amount is None or units(amount) <= 0:
            raise ApprovalError('A positive release amount is required')
        payload['amountNanoSek'] = units(amount)
    if action in ('live-order','live-cancel','session-start'):
        if not isinstance(subject,str) or not subject:
            raise ApprovalError('A ticket fingerprint is required')
        payload['subject']=subject
    body = encode(payload)
    signature = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return body+'.'+signature


def verify(key, token, action, amount=None, now=None, subject=None):
    try:
        body, signature = token.split('.')
        expected = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ApprovalError('Invalid owner signature')
        payload = json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        now = int(time.time() if now is None else now)
        if payload['action'] != action or not payload['issued'] <= now <= payload['expires']:
            raise ApprovalError('Approval expired or action mismatch')
        if payload['expires']-payload['issued'] > 120 or not payload['actor'] or not payload['nonce']:
            raise ApprovalError('Invalid approval scope')
        if action == 'release' and payload.get('amountNanoSek') != units(amount):
            raise ApprovalError('Approval amount mismatch')
        if action in ('live-order','live-cancel','session-start') and payload.get('subject')!=subject:
            raise ApprovalError('Approval ticket mismatch')
        return payload
    except ApprovalError:
        raise
    except Exception:
        raise ApprovalError('Invalid owner approval token') from None
