from __future__ import annotations

import hashlib
import hmac


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signature_ok(secret: str | None, body: bytes, header: str | None) -> bool:
    if not secret:
        return True
    if not header:
        return False
    return hmac.compare_digest(sign_body(secret, body), header.strip())
