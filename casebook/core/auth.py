"""인증 — Xano `security.*` 의 로컬 짝. 표준 라이브러리만 쓴다.

토큰: HMAC-SHA256 서명이 붙은 {id, exp}. Xano 와 형식 호환은 아니고 필요하지도 않다 —
만료 시간(86400s)과 의미(user.id 를 되찾는다)만 같으면 된다. 재로그인은 프론트가 처리한다.

비밀번호: PBKDF2-SHA256. Xano 는 bcrypt 라 **기존 해시는 이식되지 않는다** —
데이터 이관 시 사용자는 재로그인이 아니라 비밀번호 재설정이 필요하다(인계 문서에 기록).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .errors import UnauthorizedError

TOKEN_TTL_SECONDS = 86400  # .xs security.create_auth_token expiration 과 동일

_PBKDF2_ITERATIONS = 600_000


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def new_secret() -> str:
    return secrets.token_hex(32)


def create_token(secret: str, user_id: int, expiration: int = TOKEN_TTL_SECONDS) -> str:
    body = _b64e(json.dumps({"id": user_id, "exp": int(time.time()) + expiration}).encode())
    sig = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(secret: str, token: str) -> int:
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        payload = json.loads(_b64d(body))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return int(payload["id"])
    except UnauthorizedError:
        raise
    except Exception:
        raise UnauthorizedError("Invalid token.") from None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, iters, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False  # bcrypt 등 이관 불가 해시 — 재설정 경로로 보낸다
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False
