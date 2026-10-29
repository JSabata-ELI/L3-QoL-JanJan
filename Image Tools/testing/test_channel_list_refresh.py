"""The shared archiver channel list must not outlive CHANNELS_MAX_AGE_S.

On 2026-10-13 the SPIDER GDD, TPD and FOG channels were added to the archiver
mid-session and no PV picker offered them until a restart: the listing was
cached for the whole session. Now it is reused for a short while only.

Checks, offline (the download is replaced by a stand-in):

* pickers opened together share one download;
* once the listing is older than CHANNELS_MAX_AGE_S a new channel is offered;
* a failed later download serves the older listing instead of an error;
* with nothing held, a failed download still raises.

Run with:  python testing/test_channel_list_refresh.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402

OLD = ["L3-SBDP-SPIDER:FWHM"]
NEW = OLD + ["L3-SBDP-SPIDER:GDD"]

failures = []


def check(what, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + what + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(what)


calls = {"n": 0}
answer = {"list": OLD, "fail": False}


def fake_fetch(pattern="**", *, timeout=None):
    calls["n"] += 1
    if answer["fail"]:
        raise OSError("archiver unreachable")
    return list(answer["list"])


cpva.fetch_channels = fake_fetch
cpva._channels_cache.clear()

first = cpva.fetch_channels_cached("**")
second = cpva.fetch_channels_cached("**")
check("pickers opened together share one download",
      first == second == OLD and calls["n"] == 1, f"downloads={calls['n']}")

answer["list"] = NEW
stamp, names = cpva._channels_cache["**"]
cpva._channels_cache["**"] = (stamp - cpva.CHANNELS_MAX_AGE_S - 1, names)
got = cpva.fetch_channels_cached("**")
check("an older listing is asked for again and GDD appears", got == NEW, str(got))

answer["fail"] = True
stamp, names = cpva._channels_cache["**"]
cpva._channels_cache["**"] = (stamp - cpva.CHANNELS_MAX_AGE_S - 1, names)
got = cpva.fetch_channels_cached("**")
check("a failed refresh serves the listing held", got == NEW, str(got))

cpva._channels_cache.clear()
try:
    cpva.fetch_channels_cached("**")
    check("with nothing held a failure raises", False)
except OSError:
    check("with nothing held a failure raises", True)

print()
print("All checks passed." if not failures else f"{len(failures)} check(s) FAILED.")
sys.exit(1 if failures else 0)
