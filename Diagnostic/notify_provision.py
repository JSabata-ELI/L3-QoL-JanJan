"""Notification channels baked into the build.

Why this exists
---------------
The deployed build is started by many people from the share, and every copy has
to be able to send alerts without anyone configuring the channels first — and
without the SMTP password or the Webex bot token being readable in the UI or in
``monitor_config.json``.

Shipping the dev machine's ``monitor_config.json`` does not work: its secrets
are DPAPI blobs bound to one Windows account, so they decrypt to nothing for
everybody else. That is exactly why a freshly built .exe showed empty channel
settings.

So the channel settings are baked into ``notify_provision.dat``, which ships
with the build and is applied over the local config at load time
(``load_config()``), and stripped again before the local config is written
(``save_config()``), so the credentials never land on disk in a readable file.

Honest security note
--------------------
This is obfuscation, not secrecy. The blob travels with the program and the
program has to read it unattended, so the key is in the code and anyone
determined can recover the credentials from the build. What it does buy:
credentials are not visible in the Settings dialog, not in any JSON next to the
exe, and not exposed by a casual look at the files. Treat the baked bot token /
mailbox password as "shared within the group" and rotate them if a build leaves
the group.

Re-baking (do this whenever a channel setting or a credential changes, then
rebuild):

    python notify_provision.py bake

That reads this folder's ``monitor_config.json`` (secrets decrypted with your
DPAPI account) and rewrites ``notify_provision.dat`` next to it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from secrets_util import resolve_secret

BLOB_NAME = "notify_provision.dat"
_MAGIC = "DIAGPROV1"

# Settings the blob may carry. Anything outside this list is ignored on load, so
# a stale blob can never take over unrelated settings (poll interval, thresholds
# defaults, shared-list paths, …).
PROVISIONED_KEYS = (
    "teams_enabled", "email_enabled", "webex_enabled",
    "teams_webhook_url",
    "smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password",
    "email_from", "email_contacts",
    "webex_mode", "webex_webhook_url", "webex_bot_token", "webex_rooms",
    "webex_commands_enabled", "webex_command_poll_s", "webex_command_allowlist",
)

# Keys whose value is a credential: masked wherever provisioning is described
# to the user.
SECRET_KEYS = ("smtp_password", "webex_bot_token")

# Obfuscation pepper. Changing it invalidates every existing blob.
_PEPPER = b"L3-QoL Diagnostika notify provisioning v1"


def _keystream(salt: bytes, n: int) -> bytes:
    """SHA-256 counter-mode keystream over (pepper, salt)."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(_PEPPER + salt + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data: bytes, salt: bytes) -> bytes:
    ks = _keystream(salt, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def _search_dirs() -> list[Path]:
    """Where a blob may live, most specific first.

    ``sys._MEIPASS`` is the PyInstaller extraction dir (the blob added with
    --add-data), then the folder holding the exe / this module, so both the
    frozen build and a plain ``python main.py`` run find it.
    """
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        dirs.append(Path(meipass))
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent)
        dirs.append(Path(sys.executable).parent / "_internal")
    dirs.append(Path(__file__).resolve().parent)
    return dirs


def blob_path() -> Path | None:
    for d in _search_dirs():
        p = d / BLOB_NAME
        if p.is_file():
            return p
    return None


def _decode(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3 or lines[0] != _MAGIC:
        raise ValueError("not a provisioning blob")
    salt = base64.b64decode(lines[1])
    payload = base64.b64decode(lines[2])
    return json.loads(_xor(payload, salt).decode("utf-8"))


def load() -> dict:
    """Provisioned channel settings, or ``{}`` when the build carries none.

    Never raises: a missing or damaged blob must not stop the app from starting,
    it just means the channels come from the local config as before.
    """
    p = blob_path()
    if p is None:
        return {}
    try:
        data = _decode(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - never block launch
        print(f"[notify provisioning] ignoring {p}: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in PROVISIONED_KEYS}


def is_active() -> bool:
    return bool(load())


def describe(prov: dict) -> list[str]:
    """Human-readable summary lines for the Settings dialog. No secrets."""
    if not prov:
        return []
    lines = []
    if prov.get("teams_enabled"):
        lines.append("Teams: " + ("webhook configured"
                                  if prov.get("teams_webhook_url") else "no webhook"))
    if prov.get("email_enabled"):
        rcpt = [c for c in (prov.get("email_contacts") or []) if c.get("enabled")]
        lines.append(f"Email: {prov.get('smtp_host') or 'no host'} as "
                     f"{prov.get('email_from') or '?'} -> {len(rcpt)} recipient(s)")
    if prov.get("webex_enabled"):
        mode = prov.get("webex_mode", "bot")
        rooms = [r for r in (prov.get("webex_rooms") or []) if r.get("enabled")]
        names = ", ".join(r.get("name") or r.get("room_id", "")[:8] for r in rooms)
        lines.append(f"Webex: {mode} mode -> {len(rooms)} room(s)"
                     + (f" ({names})" if names else "")
                     + (", commands on" if prov.get("webex_commands_enabled") else ""))
    if not lines:
        lines.append("All channels are switched off in this build.")
    return lines


# ---------------------------------------------------------------------------
# Baking (developer side)
# ---------------------------------------------------------------------------

def bake(settings: dict, out_path: Path) -> dict:
    """Write the blob from `settings`, with secrets stored as plaintext.

    Returns the dict that was baked (secrets included) so the caller can report
    what went in. Secrets are resolved from their ``dpapi:``/``${ENV:…}`` form
    first — the whole point is a value the *other* accounts can use.
    """
    payload = {}
    for k in PROVISIONED_KEYS:
        if k not in settings:
            continue
        v = settings[k]
        if k in SECRET_KEYS:
            v = resolve_secret(v) if isinstance(v, str) else ""
        payload[k] = v
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    salt = hashlib.sha256(raw).digest()[:16]
    text = "\n".join([
        _MAGIC,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(_xor(raw, salt)).decode("ascii"),
        "",
    ])
    out_path.write_text(text, encoding="utf-8")
    return payload


def _main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    if len(argv) < 2 or argv[1] not in ("bake", "show"):
        print(__doc__)
        print("usage: python notify_provision.py bake|show [monitor_config.json]")
        return 2
    if argv[1] == "show":
        prov = load()
        p = blob_path()
        print(f"blob: {p or 'none found'}")
        for line in describe(prov) or ["(nothing provisioned)"]:
            print("  " + line)
        for k in SECRET_KEYS:
            v = prov.get(k) or ""
            print(f"  {k}: {'set (%d chars)' % len(v) if v else 'empty'}")
        return 0

    cfg = Path(argv[2]) if len(argv) > 2 else here / "monitor_config.json"
    if not cfg.is_file():
        print(f"config not found: {cfg}")
        return 1
    settings = json.loads(cfg.read_text(encoding="utf-8")).get("settings") or {}
    out = here / BLOB_NAME
    baked = bake(settings, out)
    print(f"baked {len(baked)} key(s) into {out}")
    for line in describe(baked):
        print("  " + line)
    for k in SECRET_KEYS:
        v = baked.get(k) or ""
        if k in baked and not v:
            print(f"  WARNING: {k} came out empty — could not decrypt it from "
                  f"{cfg.name} on this account.")
        elif v:
            print(f"  {k}: baked ({len(v)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
