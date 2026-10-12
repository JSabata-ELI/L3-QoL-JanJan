r"""The helper exe is deployed inside its program's folder, not beside it.

Z:\Software\Diagnostic Webex listener  ->  Z:\Software\Diagnostic\Diagnostic Webex listener
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cm_t


def test_helper_goes_inside_its_program():
    cm_t._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    dst = Path(r"Z:\Software")
    assert cm_t.dest_dir_for(dst, "Diagnostic Webex listener") == \
        dst / "Diagnostic" / "Diagnostic Webex listener"


def test_plain_program_stays_on_top():
    cm_t._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    dst = Path(r"Z:\Software")
    assert cm_t.dest_dir_for(dst, "Image Tools") == dst / "Image Tools"
    assert cm_t.dest_dir_for(dst, "Diagnostic") == dst / "Diagnostic"


def test_program_keeps_its_helper_folder():
    cm_t._HELPER_PARENTS = {"diagnostic webex listener": "Diagnostic"}
    assert cm_t.helper_dir_names("Diagnostic") == {"diagnostic webex listener"}
    assert cm_t.helper_dir_names("Image Tools") == set()


def test_real_config_lists_the_listener():
    """Read from the repo's own build_config.json files, not a stub."""
    cm_t._HELPER_PARENTS = None
    mapping = cm_t._helper_parents(refresh=True)
    assert mapping.get("diagnostic webex listener") == "Diagnostic", mapping


def test_legacy_folder_is_moved(tmp_path):
    old = tmp_path / "Diagnostic Webex listener"
    (old / "archive").mkdir(parents=True)
    (old / "Diagnostic Webex listener v1.0.0.exe").write_text("x")
    new = tmp_path / "Diagnostic" / "Diagnostic Webex listener"
    cm_t._move_legacy_toplevel_folder(old, new, lambda m: None, lambda *a: None)
    assert not old.exists()
    assert (new / "Diagnostic Webex listener v1.0.0.exe").exists()


def test_legacy_folder_is_merged_when_target_exists(tmp_path):
    old = tmp_path / "Diagnostic Webex listener"
    old.mkdir()
    (old / "ReadMe_Diagnostic Webex listener.txt").write_text("doc")
    new = tmp_path / "Diagnostic" / "Diagnostic Webex listener"
    new.mkdir(parents=True)
    (new / "Diagnostic Webex listener v1.1.0.exe").write_text("x")
    cm_t._move_legacy_toplevel_folder(old, new, lambda m: None, lambda *a: None)
    assert not old.exists()
    assert (new / "ReadMe_Diagnostic Webex listener.txt").read_text() == "doc"
    assert (new / "Diagnostic Webex listener v1.1.0.exe").exists()
