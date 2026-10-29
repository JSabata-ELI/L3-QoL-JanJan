"""Build speed: phase timing, no automatic --collect-all, safe parallel builds.

Run:  py -m pytest testing/test_build_speed.py -q
"""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dt_core as core  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    dist = tmp_path / "dist"
    for d in (repo, dist):
        d.mkdir()
    monkeypatch.setattr(core, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    monkeypatch.setattr(core, "PRACTICE_ROOT", tmp_path / "practice")
    monkeypatch.setattr(core, "state_dir", lambda: tmp_path / "state")
    (tmp_path / "state").mkdir()
    core.invalidate_versions_cache()
    core.save_config({"root_folder": str(repo), "dist_root": str(dist),
                      "scratch": str(tmp_path / "Scratch"),
                      "sharepoint": str(tmp_path / "SharePoint")})
    app = repo / "App"
    app.mkdir()
    (app / "App.py").write_text("import numpy\nimport matplotlib\nimport scipy\n",
                                encoding="utf-8")
    return {"repo": repo, "dist": dist, "app": app}


def _captured_args(env, monkeypatch) -> list[str]:
    seen = {}

    def fake_run(args, cwd, log, on_line=None):
        seen["args"] = args
        return 1  # stop right after the PyInstaller call

    monkeypatch.setattr(core, "_run_logged", fake_run)
    ok, _ = core.build_one_project(env["app"], "1.0.0", env["dist"], env["repo"])
    assert not ok
    return seen["args"]


def test_no_automatic_collect_all(env, monkeypatch):
    args = _captured_args(env, monkeypatch)
    assert "--collect-all" not in args
    assert "numpy.tests" not in args


def test_every_source_is_analysed(env, monkeypatch):
    """A tab loaded from its file at run time still has its imports found."""
    (env["app"] / "is_t.py").write_text("from scipy.ndimage import zoom\n", encoding="utf-8")
    (env["app"] / "test_x.py").write_text("", encoding="utf-8")
    args = _captured_args(env, monkeypatch)
    hidden = [args[i + 1] for i, a in enumerate(args) if a == "--hidden-import"]
    assert "is_t" in hidden
    assert "test_x" not in hidden and "App" not in hidden


def test_collect_all_from_build_config_still_honoured(env, monkeypatch):
    (env["app"] / "build_config.json").write_text(
        json.dumps({"collect_all": ["numpy"]}), encoding="utf-8")
    args = _captured_args(env, monkeypatch)
    i = args.index("--collect-all")
    assert args[i + 1] == "numpy"
    assert "numpy.tests" in args


def test_phase_clock_follows_pyinstaller_lines(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(core.time, "perf_counter", lambda: now[0])
    clock = core.PhaseClock()
    for t, line in [(1, "123 INFO: Analyzing modules"),
                    (40, "4000 INFO: Building PYZ (ZlibArchive) PYZ-00.pyz"),
                    (50, "5000 INFO: Building EXE from EXE-00.toc"),
                    (55, "5500 INFO: Building COLLECT COLLECT-00.toc"),
                    # an earlier phase's word later on never goes back
                    (60, "6000 INFO: checking PYZ")]:
        now[0] = t
        clock.on_line(line)
    now[0] = 70
    clock.enter("finish")
    now[0] = 72
    s = clock.stop()
    assert s == {"analysis": 40, "package": 15, "collect": 15, "finish": 2}
    assert clock.short() == "analysis 40s, package 15s, collect 15s, finish 2s"


def test_build_times_log_one_line_per_build(env):
    core.record_build_times("App", "v1.0.0", 72.0,
                            {"analysis": 40, "package": 15, "collect": 15, "finish": 2}, 3.5)
    core.record_build_times("App", "v1.0.1", 10.0, None)
    lines = core.build_times_path().read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == ["date", "program", "version", "total", "analysis",
                                    "package", "collect", "finish", "copy"]
    assert lines[1].split("\t")[1:] == ["App", "v1.0.0", "72.0", "40.0", "15.0", "15.0",
                                        "2.0", "3.5"]
    assert lines[2].split("\t")[-1] == ""


def test_parallel_writers_lose_nothing(env, tmp_path, monkeypatch):
    versions = tmp_path / "Versions.txt"
    monkeypatch.setattr(core, "versions_txt_path", lambda: versions)
    names = [f"P{i}" for i in range(12)]

    def one(n):
        core.write_version_to_txt(n, "1.0.0")
        core.record_build_usage(n)

    threads = [threading.Thread(target=one, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = versions.read_text(encoding="utf-8")
    assert all(f"{n} = v1.0.0" in text for n in names)
    events = json.loads(core.usage_log_path().read_text(encoding="utf-8"))["events"]
    assert sorted(e["project"] for e in events) == sorted(names)


def test_parallel_builds_bounded(monkeypatch):
    monkeypatch.setattr(core.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(core, "_free_ram_gb", lambda: 20.0)
    assert core.parallel_builds(1) == 1
    assert core.parallel_builds(5) == 3
    monkeypatch.setattr(core, "_free_ram_gb", lambda: 3.0)
    assert core.parallel_builds(5) == 1
    monkeypatch.setattr(core.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(core, "_free_ram_gb", lambda: 20.0)
    assert core.parallel_builds(5) == 1
