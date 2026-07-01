"""Secret obfuscation helpers for monitor_config.json.

A password / bot token can be stored three ways in the config:
  - literal plaintext                (legacy, not recommended)
  - "${ENV:NAME}"  -> read from an environment variable at send time
  - "dpapi:<base64>" -> encrypted with Windows DPAPI, tied to the current
                        Windows user account. Decrypts transparently for that
                        user; unreadable to anyone else or on another machine.

encrypt_secret() runs when settings are saved; resolve_secret() when the
password / token is actually used. Both degrade gracefully if pywin32 or DPAPI
is unavailable (store / return the plaintext) so the app never breaks.
"""

from __future__ import annotations

import base64
import os

_DPAPI_PREFIX = "dpapi:"

try:
    import win32crypt  # type: ignore
except Exception:  # pragma: no cover - non-Windows / pywin32 missing
    win32crypt = None


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_DPAPI_PREFIX)


def encrypt_secret(value: str) -> str:
    """Encrypt a plaintext secret with Windows DPAPI (current user).

    Returns a ``dpapi:<base64>`` string. Empty values, values already
    encrypted, and ``${ENV:...}`` references are returned unchanged. If DPAPI
    is unavailable the plaintext is returned as-is (best effort).
    """
    if not value or is_encrypted(value) or value.startswith("${ENV:"):
        return value
    if win32crypt is None:
        return value
    try:
        blob = win32crypt.CryptProtectData(
            value.encode("utf-8"), None, None, None, None, 0)
        return _DPAPI_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return value


def resolve_secret(value: str) -> str:
    """Return the usable plaintext for a stored secret. Never raises.

    Handles ``${ENV:NAME}`` references and ``dpapi:`` blobs; anything else is
    treated as literal plaintext.
    """
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("${ENV:") and value.endswith("}"):
        return os.environ.get(value[6:-1], "")
    if is_encrypted(value):
        if win32crypt is None:
            return ""
        try:
            raw = base64.b64decode(value[len(_DPAPI_PREFIX):])
            _, data = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
            return data.decode("utf-8")
        except Exception:
            return ""
    return value
