"""
Security primitives: encryption-at-rest, secret masking, and log scrubbing.

Three jobs, all small:

* **Encrypt** anything we persist (OAuth tokens, user-supplied LLM keys) with
  Fernet symmetric encryption. The key comes from ``THETA_SECRET_KEY``; for local
  development we generate one and persist it so sessions survive a restart.
* **Mask** secrets for display (``sk-abcd••••••wxyz``) so the UI can show that a
  key is set without revealing it.
* **Scrub** secrets from logs. Any secret value we hold is registered and
  redacted from log records, plus a few common credential patterns (Bearer
  tokens, ``access_token=…`` query params) as defence in depth.

Nothing here ever writes a secret to disk in plaintext or returns it to the
browser.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import threading

from cryptography.fernet import Fernet, InvalidToken

from config import settings

_KEY_FILE = settings.data_dir / ".secret_key"
_lock = threading.Lock()
_fernet: Fernet | None = None
_log = logging.getLogger("theta.security")


# --------------------------------------------------------------------------- #
# Encryption                                                                  #
# --------------------------------------------------------------------------- #
def _derive_key(material: str) -> bytes:
    """Accept either a ready-made Fernet key or an arbitrary passphrase and
    return a valid 32-byte urlsafe-base64 Fernet key."""
    material = material.strip()
    if len(material) == 44:  # a urlsafe-b64 32-byte key is exactly 44 chars
        try:
            Fernet(material.encode())  # validates length + charset
            return material.encode()
        except Exception:
            pass
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _load_or_create_key() -> bytes:
    if settings.secret_key:
        return _derive_key(settings.secret_key)
    # No configured key: reuse a persisted local key, or mint and store one.
    try:
        if _KEY_FILE.exists():
            return _KEY_FILE.read_text(encoding="utf-8").strip().encode()
    except Exception:
        pass
    key = Fernet.generate_key()
    try:
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_text(key.decode(), encoding="utf-8")
        try:
            os.chmod(_KEY_FILE, 0o600)
        except Exception:
            pass
        _log.info("Generated a local encryption key at %s (set THETA_SECRET_KEY "
                  "to control it in production).", _KEY_FILE.name)
    except Exception:
        _log.warning(
            "Could not persist a local secret key; encrypted sessions will not "
            "survive a restart. Set THETA_SECRET_KEY for stable encryption."
        )
    return key


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _lock:
            if _fernet is None:
                _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


def try_decrypt(token: str) -> str | None:
    """Decrypt, returning None instead of raising on tampered/expired input."""
    try:
        return decrypt(token)
    except (InvalidToken, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Masking                                                                     #
# --------------------------------------------------------------------------- #
def mask_secret(value: str, show: int = 4) -> str:
    """Return a display-safe rendering: first/last few chars, middle hidden."""
    if not value:
        return ""
    v = value.strip()
    if len(v) <= show * 2:
        return "•" * len(v)
    return f"{v[:show]}{'•' * 6}{v[-show:]}"


# --------------------------------------------------------------------------- #
# Log scrubbing                                                               #
# --------------------------------------------------------------------------- #
_registered: set[str] = set()
_reg_lock = threading.Lock()
_REDACTED = "«redacted»"

_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9\-_\.=]+"),
    re.compile(r"(?i)(\"?access_token\"?\s*[:=]\s*\"?)[A-Za-z0-9\-_\.]+"),
    re.compile(r"(?i)(\"?refresh_token\"?\s*[:=]\s*\"?)[A-Za-z0-9\-_\.]+"),
    re.compile(r"(?i)([?&](?:key|api_key|access_token|id_token)=)[^&\s\"']+"),
]


def register_secret(value: str | None) -> None:
    """Remember a secret so it is redacted if it ever appears in a log line."""
    if value and isinstance(value, str) and len(value) >= 8:
        with _reg_lock:
            _registered.add(value)


def scrub(text: str) -> str:
    if not text:
        return text
    out = text
    with _reg_lock:
        snapshot = list(_registered)
    for s in snapshot:
        if s and s in out:
            out = out.replace(s, _REDACTED)
    for pat in _PATTERNS:
        out = pat.sub(rf"\1{_REDACTED}", out)
    return out


class SecretScrubFilter(logging.Filter):
    """Rewrites each log record's rendered message with secrets redacted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            scrubbed = scrub(msg)
            if scrubbed != msg:
                record.msg = scrubbed
                record.args = ()
        except Exception:
            pass
        return True


_scrub_installed = False


def install_log_scrubber() -> None:
    """Attach the scrubbing filter across the logging tree (idempotent)."""
    global _scrub_installed
    if _scrub_installed:
        return
    filt = SecretScrubFilter()
    root = logging.getLogger()
    root.addFilter(filt)
    for h in root.handlers:
        h.addFilter(filt)
    # Cover loggers that keep their own handlers (uvicorn, etc.).
    for name in list(logging.root.manager.loggerDict.keys()):
        lg = logging.getLogger(name)
        for h in getattr(lg, "handlers", []):
            h.addFilter(filt)
    _scrub_installed = True
