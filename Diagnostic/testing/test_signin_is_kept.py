"""A sign-in that has been proved to work must be SAVED, and the old verdict dropped.

The bug this pins, seen live on 2026-09-10: the two Settings sign-in buttons put
the sign-in into a text box and verified it, but nothing reached the disk until
the whole dialog was confirmed with Save. So the dialog said "The sign-in works"
while the bot, the background jobs and the always-on listener all went on using
the expired cookie still on disk — and the listener never sees that dialog at
all, so nothing could ever have healed it there.

Two things are checked: the sign-in is written the moment the test succeeds, and
every field that quotes the "expired" verdict is cleared with it.

No Qt window on screen, no portal, and the real settings file is never touched.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}\n       got  {got!r}\n       want {want!r}")


import okbase_menu as om                            # noqa: E402

# The real okbase.json is never opened.
_FAKE_DISK: dict = {}
om.load_user_settings = lambda: dict(_FAKE_DISK)
om.save_user_settings = lambda values: (_FAKE_DISK.update(values), "")[1]

import monitor_tab as mt                            # noqa: E402
from secrets_util import resolve_secret             # noqa: E402


class FakeField:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value


class FakeWin:
    def __init__(self):
        self.settings = {"okbase_session_cookie": "OLD-AND-EXPIRED"}
        self._menu_error = "the saved OKbase session has expired — sign in again"
        self._menu_last_ok = False
        self._menu_last_decisive = False
        self._menu_last_check = None
        self.logged: list[str] = []
        self.jobs: list[str] = []
        self._menu_cache = {}

    _menu_signin_recovered = mt.MonitorWidget._menu_signin_recovered
    _food_reply = mt.MonitorWidget._food_reply

    def _log(self, text):
        self.logged.append(text)

    def _start_menu_job(self, mode):
        self.jobs.append(mode)
        return True


class FakeDialog:
    """Just enough of SettingsDialog to exercise _keep_working_signin."""

    _keep_working_signin = mt.SettingsDialog._keep_working_signin

    def __init__(self, cookie="JSESSIONID=fresh; _shibsession_x=y"):
        self._win = FakeWin()
        self.okbase_cookie = FakeField(cookie)
        self.notes: list[str] = []

    def _okbase_note(self, text):
        self.notes.append(text)


# --------------------------------------------------------------------------- #
print("a sign-in proved to work is saved at once")

_FAKE_DISK.clear()
dlg = FakeDialog()
kept = dlg._keep_working_signin()
check("it reports that it saved", kept, True)
check("the file now holds a sign-in",
      bool(_FAKE_DISK.get("okbase_session_cookie")), True)
check("and it is the one that was tested",
      resolve_secret(_FAKE_DISK["okbase_session_cookie"]),
      "JSESSIONID=fresh; _shibsession_x=y")
check("it is not stored in the clear",
      _FAKE_DISK["okbase_session_cookie"] == "JSESSIONID=fresh; _shibsession_x=y",
      False)
check("the running app was told too",
      resolve_secret(dlg._win.settings["okbase_session_cookie"]),
      "JSESSIONID=fresh; _shibsession_x=y")

print("")
print("and the stale verdict goes with it")
check("nothing left to quote", dlg._win._menu_error, "")
check("the last contact counts as good", dlg._win._menu_last_ok, True)
check("and as decisive", dlg._win._menu_last_decisive, True)
check("its time is now",
      isinstance(dlg._win._menu_last_check, datetime), True)
check("the menu is read again", dlg._win.jobs, ["fetch"])
check("and the log says so",
      any("works and has been saved" in line for line in dlg._win.logged), True)

# This is the sentence the operator kept seeing. It must be gone.
reply = dlg._win._food_reply("")
check("/food no longer claims the sign-in has expired",
      "has expired" in reply, False)

print("")
print("what a paste brought with it is kept too")

_FAKE_DISK.clear()
dlg = FakeDialog()
dlg._pasted_user_id = 6507822
dlg._pasted_canteen_id = 1
dlg._pasted_filter = {"userId": 6507822, "objednavky": {}}
dlg._keep_working_signin()
check("the OKbase id", _FAKE_DISK.get("okbase_user_id"), 6507822)
check("which canteen", _FAKE_DISK.get("okbase_canteen_id"), 1)
check("and the request template",
      _FAKE_DISK.get("okbase_filter"), {"userId": 6507822, "objednavky": {}})

print("")
print("what it must NOT do")

# Everything else the operator may have typed still waits for Save: a cancelled
# dialog must not rewrite the settings. Only the sign-in is a fact.
_FAKE_DISK.clear()
dlg = FakeDialog()
dlg._keep_working_signin()
check("only the sign-in is written", sorted(_FAKE_DISK),
      ["okbase_session_cookie"])

_FAKE_DISK.clear()
dlg = FakeDialog(cookie="")
check("an empty box saves nothing", dlg._keep_working_signin(), False)
check("and writes nothing", _FAKE_DISK, {})
check("and does not claim the sign-in came back",
      dlg._win._menu_error != "", True)

# A file that cannot be written must not be reported as saved — otherwise the
# operator closes the dialog believing it is kept.
_FAKE_DISK.clear()
_real_save = om.save_user_settings
om.save_user_settings = lambda values: "the settings file is read-only"
try:
    dlg = FakeDialog()
    dlg._warned: list[str] = []
    mt.QMessageBox.warning = lambda *a, **k: dlg._warned.append(a[-1])
    check("a failed write is not called saved", dlg._keep_working_signin(), False)
    check("and the operator is told to press Save",
          any("Press Save" in w for w in dlg._warned), True)
    check("and the old verdict is left alone", dlg._win._menu_error != "", True)
finally:
    om.save_user_settings = _real_save


print("")
if FAILED:
    print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
    raise SystemExit(1)
print("all ok")
