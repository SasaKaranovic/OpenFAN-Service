"""Optional single-password login.

Off by default: the service is meant to run on a trusted home network. When
enabled, the web UI and API need a session cookie. ``/api/health`` and push
endpoints (which use their own per-source token) stay open.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

COOKIE = "openfan_session"
SESSION_DAYS = 30
_ITER = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


class SessionSigner:
    def __init__(self, config_dir: Path) -> None:
        self.path = Path(config_dir) / ".session-secret"
        self.key = self._load()

    def _load(self) -> bytes:
        try:
            key = self.path.read_bytes().strip()
            if len(key) >= 32:
                return key
        except OSError:
            pass
        key = secrets.token_hex(32).encode()
        try:
            self.path.write_bytes(key)
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # read-only config dir: sessions just will not survive restarts
        return key

    def issue(self, password_hash: str) -> str:
        expires = int(time.time()) + SESSION_DAYS * 86400
        # Binding the token to the password hash logs everyone out when the password changes.
        sig = hmac.new(self.key, f"{expires}:{password_hash}".encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{sig}"

    def valid(self, token: str | None, password_hash: str) -> bool:
        if not token or "." not in token:
            return False
        expires, sig = token.split(".", 1)
        try:
            if int(expires) < time.time():
                return False
        except ValueError:
            return False
        good = hmac.new(self.key, f"{expires}:{password_hash}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good)
