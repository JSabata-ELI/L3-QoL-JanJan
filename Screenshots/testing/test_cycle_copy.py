"""One cycle's worth of camera copying, against a fake archive on disk.

Covers what the real run showed: all cameras answer for the SAME recorded
moment, a frame from after that moment is never taken, the files come out in
camera order however the threads finish, and the skip rule decides what is left
out.

Run:  python testing/test_cycle_copy.py
"""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

SEC = 1_000_000_000
BASE = 1_757_000_000 * SEC
FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


class FakeApp:
    """Just enough of App to run the camera copying off-line."""
    _copy_one_camera = s.App._copy_one_camera
    _copy_cameras = s.App._copy_cameras
    _bump_label_index = s.App._bump_label_index

    def __init__(self, folders):
        self._cam_dir_cache = {}
        self._cam_dir_cache_time = {}
        self._cam_dir_cache_ttl = 5.0
        self._cam_dir_cache_lock = threading.Lock()
        self._label_settings = {}
        self._auto_last_frame = {}
        self._auto_copy_active = True
        self._cam_folder_cache = None
        self._folders = folders
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)

    def after(self, _ms, fn):
        fn()

    def _resolve_cam_folders(self, _target_ns, cams):
        return {c: self._folders[c] for c in cams if c in self._folders}


def build_archive(tmp: Path, plan: dict) -> dict:
    """plan: cam -> list of frame offsets in seconds from BASE."""
    folders = {}
    for cam, offsets in plan.items():
        d = tmp / "archive" / cam
        d.mkdir(parents=True, exist_ok=True)
        for off in offsets:
            ts = BASE + int(off * SEC)
            (d / f"{cam}_{ts}.png").write_bytes(b"png")
        folders[cam] = d
    return folders


def test_one_moment():
    print("all cameras answer for one recorded moment")
    cams = ["PAP1_NF", "PAP1_DF", "PAM5_NF", "PAM10_NF", "PAM9_NF", "PAM12_NF"]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        out.mkdir()
        # Every camera has frames around the moment; PAM12 also has one 7 s
        # LATER — the old code took that one because it was nearest in absolute
        # terms, which is how a cycle ended up spread over ten seconds.
        folders = build_archive(tmp, {
            "PAP1_NF": [8, 9, 10],
            "PAP1_DF": [9.5, 10],
            "PAM5_NF": [7, 9.8],
            "PAM10_NF": [9.9],
            "PAM9_NF": [6, 9.2],
            "PAM12_NF": [9.1, 17],
        })
        app = FakeApp(folders)
        target = BASE + 10 * SEC
        res = app._copy_cameras(cams, "cpva", target, out, "", len(cams), cycle=1)

        check("every camera delivered a file", len(res["copied"]) == 6)
        check("no problems reported", res["problems"] == [])
        check("files come out in camera order",
              [p.name.split("__")[0] for p in res["copied"]] == cams)
        check("no frame from after the moment",
              all(a is not None and a >= 0 for a in res["ages"].values()))
        check("every frame is within a second of the moment",
              max(res["ages"].values()) <= 1 * SEC)
        check("the late PAM12 frame was not used", res["ages"]["PAM12_NF"] == int(0.9 * SEC))
        check("files really exist", all(p.exists() for p in res["copied"]))
        check("the frame's own time is in the name",
              all("2025-09-04" in p.name or "__20" in p.name for p in res["copied"]))


def test_skip_and_hold():
    print("a camera that stopped storing")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        out.mkdir()
        # PAM9 stopped 30 s ago, PAP1 is current.
        folders = build_archive(tmp, {"PAP1_NF": [59.5], "PAM9_NF": [30]})
        app = FakeApp(folders)
        cams = ["PAP1_NF", "PAM9_NF"]
        target = BASE + 60 * SEC

        res = app._copy_cameras(cams, "cpva", target, out, "", 2, cycle=1)
        check("the stale camera is skipped", len(res["copied"]) == 1)
        check("and says why", any("skipped" in p for p in res["problems"]))

        # Next cycle: the same stale frame is now the one the previous cycle saw,
        # so it is kept — a camera standing still still has to show up.
        res2 = app._copy_cameras(cams, "cpva", target + 5 * SEC, out, "", 2, cycle=2)
        check("the same stale frame is kept on the next cycle",
              len(res2["copied"]) == 2 and res2["problems"] == [])

        # The manual Copy button has no cycle, so nothing is ever skipped.
        app._auto_last_frame = {}
        res3 = app._copy_cameras(cams, "cpva", target, out, "", 2, cycle=None)
        check("manual Copy never skips", len(res3["copied"]) == 2)


def test_missing_camera():
    print("cameras that cannot be served")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        out.mkdir()
        folders = build_archive(tmp, {"PAP1_NF": [9]})
        app = FakeApp(folders)
        res = app._copy_cameras(["PAP1_NF", "PAM5_NF", "NoSuchCam"], "cpva",
                                BASE + 10 * SEC, out, "", 3, cycle=1)
        check("the one available camera is copied", len(res["copied"]) == 1)
        check("an unknown camera is reported",
              any("don't know" in p for p in res["problems"]))
        check("a camera missing from the archive is reported",
              any("not found in the archive" in p for p in res["problems"]))


if __name__ == "__main__":
    test_one_moment()
    test_skip_and_hold()
    test_missing_camera()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")
