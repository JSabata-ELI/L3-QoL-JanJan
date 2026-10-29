r"""The helper exe is deployed inside its program's folder, not beside it.

Z:\Software\Diagnostic Webex listener  ->  Z:\Software\Diagnostic\Diagnostic Webex listener
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dt_core


def test_helper_goes_inside_its_program():
    dt_core._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    dst = Path(r"Z:\Software")
    assert dt_core.dest_dir_for(dst, "Diagnostic Webex listener") == \
        dst / "Diagnostic" / "Diagnostic Webex listener"


def test_plain_program_stays_on_top():
    dt_core._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    dst = Path(r"Z:\Software")
    assert dt_core.dest_dir_for(dst, "Image Tools") == dst / "Image Tools"
    assert dt_core.dest_dir_for(dst, "Diagnostic") == dst / "Diagnostic"


def test_program_keeps_its_helper_folder():
    dt_core._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    assert dt_core.helper_dir_names("Diagnostic") == {"diagnostic webex listener"}
    assert dt_core.helper_dir_names("Image Tools") == set()


def test_real_config_lists_the_listener():
    """Read from the repo's own build_config.json files, not a stub."""
    dt_core._HELPER_PARENTS = None
    mapping = dt_core.helper_parents(refresh=True)
    assert mapping.get("diagnostic webex listener") == "Diagnostic", mapping


def test_legacy_folder_is_moved(tmp_path):
    old = tmp_path / "Diagnostic Webex listener"
    (old / "archive").mkdir(parents=True)
    (old / "Diagnostic Webex listener v1.0.0.exe").write_text("x")
    new = tmp_path / "Diagnostic" / "Diagnostic Webex listener"
    dt_core._move_legacy_toplevel_folder(old, new, lambda m: None, lambda *a: None)
    assert not old.exists()
    assert (new / "Diagnostic Webex listener v1.0.0.exe").exists()


def test_diagnostic_and_listener_skip_the_scratch(monkeypatch):
    dt_core._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    scratch, share = Path(r"Z:\Software"), Path(r"S:\QoL")
    monkeypatch.setattr(dt_core, "scratch_root", lambda: scratch)
    for name in ("Diagnostic", "Diagnostic Webex listener"):
        assert dt_core.kept_off_scratch(name)
        assert dt_core.roots_for(name, [scratch, share]) == [share]
    assert dt_core.roots_for("Image Tools", [scratch, share]) == [scratch, share]


def test_skipped_scratch_is_not_a_partial_copy(monkeypatch):
    dt_core._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    scratch, share = Path(r"Z:\Software"), Path(r"S:\QoL")
    monkeypatch.setattr(dt_core, "scratch_root", lambda: scratch)
    per = [{"name": "Diagnostic", "version": "v1.0.0", "dest_ok": 1, "error": None}]
    text = dt_core.overall_summary(per, [scratch, share], 1.0, 0)
    assert "copied to 1 of 1 destinations" in text


def test_legacy_folder_is_merged_when_target_exists(tmp_path):
    old = tmp_path / "Diagnostic Webex listener"
    old.mkdir()
    (old / "ReadMe_Diagnostic Webex listener.txt").write_text("doc")
    new = tmp_path / "Diagnostic" / "Diagnostic Webex listener"
    new.mkdir(parents=True)
    (new / "Diagnostic Webex listener v1.1.0.exe").write_text("x")
    dt_core._move_legacy_toplevel_folder(old, new, lambda m: None, lambda *a: None)
    assert not old.exists()
    assert (new / "ReadMe_Diagnostic Webex listener.txt").read_text() == "doc"
    assert (new / "Diagnostic Webex listener v1.1.0.exe").exists()
