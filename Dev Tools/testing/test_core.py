"""Core logic of the Qt Dev Tools, run on throw-away folders (no UI, no shares).

Run:  py -m pytest testing/test_core.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dt_core as core  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake repo, dist and two destinations; config and state redirected."""
    repo = tmp_path / "repo"
    dist = tmp_path / "dist"
    scratch = tmp_path / "Scratch"
    share = tmp_path / "SharePoint"
    for d in (repo, dist, scratch, share):
        d.mkdir()
    monkeypatch.setattr(core, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    monkeypatch.setattr(core, "PRACTICE_ROOT", tmp_path / "practice")
    # The state files (groups, last deployed, build history) live beside the
    # app; a test must never write the real ones.
    monkeypatch.setattr(core, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(core, "_HELPER_PARENTS", None)
    core.invalidate_versions_cache()
    core.save_config({"root_folder": str(repo), "dist_root": str(dist),
                      "scratch": str(scratch), "sharepoint": str(share)})

    app = repo / "App"
    app.mkdir()
    (app / "App.py").write_text("import app_t\n", encoding="utf-8")
    (app / "app_t.py").write_text("x = 1\n", encoding="utf-8")
    (app / "listener.py").write_text("print(1)\n", encoding="utf-8")
    (app / "ReadMe_App.txt").write_text("short", encoding="utf-8")
    (app / "ReadMe_App_Full.txt").write_text("long", encoding="utf-8")
    (app / "build_config.json").write_text(json.dumps(
        {"extra_exes": [{"script": "listener.py", "name": "App Listener"}]}), encoding="utf-8")
    return {"repo": repo, "dist": dist, "scratch": scratch, "share": share, "app": app}


def _fake_build(dist: Path, name: str, ver: str, assets: bool = True) -> Path:
    vdir = dist / name / ver
    vdir.mkdir(parents=True)
    (vdir / f"{name} {ver}.exe").write_bytes(b"MZ" + ver.encode())
    (vdir / f"{name} {ver}.py").write_text("main", encoding="utf-8")
    (vdir / "app_t.py").write_text("helper", encoding="utf-8")
    (vdir / "icon.ico").write_bytes(b"ico")
    if assets:
        (vdir / "images").mkdir()
        (vdir / "images" / "a.png").write_bytes(b"png")
    return vdir


def test_discovery_and_helpers(env):
    projects = core.find_projects(env["repo"])
    assert [p.name for p in projects] == ["App"]
    assert core.guess_main_py(env["app"]).name == "App.py"
    helpers = core.find_helpers(projects)
    assert [h["name"] for h in helpers["App"]] == ["App Listener"]
    assert core.dest_dir_for(env["scratch"], "App Listener") == env["scratch"] / "App" / "App Listener"
    assert core.dest_dir_for(env["scratch"], "App") == env["scratch"] / "App"


def test_versions_txt_round_trip(env):
    assert core.last_built_version("App", env["dist"]) == (None, True)
    core.write_version_to_txt("App", "1.2.3")
    assert core.read_versions_txt(force=True)["App"] == "v1.2.3"
    assert core.last_built_version("App", env["dist"]) == ("1.2.3", False)
    assert core.bump_patch("1.2.3") == "1.2.4"


def test_module_home_check_catches_copy_elsewhere(env):
    other = env["repo"] / "Other"
    other.mkdir()
    (other / "Other.py").write_text("", encoding="utf-8")
    (other / "app_t.py").write_text("", encoding="utf-8")
    ok, msg = core.check_module_homes(env["app"], [env["app"] / "App.py", env["app"] / "app_t.py"],
                                      env["repo"])
    assert not ok and "more than one folder" in msg


def test_deploy_then_redeploy_archives_old(env):
    _fake_build(env["dist"], "App", "v1.0.0")
    roots = [env["scratch"], env["share"]]
    stats, _ = core.deploy_one_program(env["dist"] / "App", "v1.0.0", roots)
    assert stats["dest_ok"] == 2 and stats["exe_copied"] == 2
    tgt = env["scratch"] / "App"
    assert (tgt / "App v1.0.0.exe").exists()
    assert (tgt / "ReadMe_App.txt").exists() and (tgt / "ReadMe_App_Full.txt").exists()
    assert (tgt / "images" / "a.png").exists()

    # Things the deploy must keep: shared data, a helper folder; and remove: a stale folder.
    (tgt / "shared").mkdir()
    (tgt / "shared" / "state.json").write_text("{}", encoding="utf-8")
    (tgt / "junk").mkdir()
    core.helper_parents(refresh=True)
    (tgt / "App Listener").mkdir()

    _fake_build(env["dist"], "App", "v1.0.1", assets=False)
    stats, _ = core.deploy_one_program(env["dist"] / "App", "v1.0.1", roots)
    assert stats["archived_exe"] == 2
    assert (tgt / "App v1.0.1.exe").exists() and not (tgt / "App v1.0.0.exe").exists()
    assert (tgt / "archive" / "v1.0.0" / "App v1.0.0.exe").exists()
    # the unversioned helper module went into the live version's archive folder
    assert (tgt / "archive" / "v1.0.0" / "app_t.py").exists()
    assert (tgt / "shared" / "state.json").exists()
    assert (tgt / "App Listener").exists()
    assert not (tgt / "junk").exists()
    # images/ is not in v1.0.1 and not in the repo — still never deleted
    assert (tgt / "images" / "a.png").exists()


def test_helper_deploys_inside_parent(env):
    _fake_build(env["dist"], "App Listener", "v1.0.0", assets=False)
    core.helper_parents(refresh=True)
    stats, _ = core.deploy_one_program(env["dist"] / "App Listener", "v1.0.0",
                                       [env["scratch"]], {"App Listener": env["app"]})
    assert stats["dest_ok"] == 1
    assert (env["scratch"] / "App" / "App Listener" / "App Listener v1.0.0.exe").exists()
    assert not (env["scratch"] / "App Listener").exists()


def test_icon_conflict_scan(env):
    _fake_build(env["dist"], "App", "v1.0.0")
    (env["scratch"] / "App").mkdir()
    (env["scratch"] / "App" / "icon.ico").write_bytes(b"different size")
    found = core.scan_icon_conflicts([(env["dist"] / "App", "v1.0.0")], [env["scratch"], env["share"]])
    name, src, conflict, exists = found[0]
    assert conflict == env["scratch"] / "App" / "icon.ico"
    assert exists == {env["scratch"]: True, env["share"]: False}
    no_conflict = core.icon_decisions_for(name, exists, None)
    assert no_conflict == {f"App|{env['scratch']}": False, f"App|{env['share']}": True}


def test_fix_archive_sorts_flat_files(env):
    arch = env["scratch"] / "App" / "archive"
    arch.mkdir(parents=True)
    (arch / "App v1.0.0__20260101_101010.exe").write_bytes(b"old")
    (arch / "App v1.0.0__20260202_101010.exe").write_bytes(b"new")
    (arch / "App v0.9.0.exe").write_bytes(b"x")
    core.fix_archive_dir(arch)
    assert (arch / "v1.0.0" / "App v1.0.0__20260202_101010.exe").exists()
    assert not (arch / "App v1.0.0__20260101_101010.exe").exists()
    assert (arch / "v0.9.0" / "App v0.9.0.exe").exists()
    core.normalize_version_folder_names(arch)
    assert (arch / "v1.0.0" / "App v1.0.0.exe").exists()


def test_reports_name_every_program(env):
    per = [{"name": "App", "version": "v1.0.1", "dest_ok": 2, "error": None}]
    info = {"items": [{"name": "App", "version": "v1.0.1", "seconds": 3.0, "out_dir": "C:/x"},
                      {"name": "Other", "version": "v2.0.0", "seconds": 1.0, "out_dir": ""}],
            "failed": [], "elapsed": 4.0}
    text = core.overall_summary(per, [env["scratch"], env["share"]], 1.0, 0, info)
    assert "✓  App  v1.0.1" in text
    assert "NOT copied — not selected in Copy Manager" in text
