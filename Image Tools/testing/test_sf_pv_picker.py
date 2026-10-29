"""The Shot Finder picks its PVs in the Image Slider's picker, out of one registry.

Asked for on 25.09.2026: *"přidej do shot finderu možnost mít custom PVčka, stejně
jak v image slideru... pod PVs záložkou bude tlačítko configure nebo něco a potom už
jen seznam PVček, které user chce hledat a které tam má nastavené"*, with two
qualifications — *"sloupce min max jsou v shot finderu zbytečné... column what it is
je taky zbytečný"* — and, on the eye, *"pokud je zaškrtlé, tak se bude ukazovat v tom
seznamu pod tlačítkem"*.

So this tab no longer has a PV search of its own:

  * **⚙ Configure PVs** opens `is_t.PvConfigDialog`, with the alarm thresholds and the
    "What it is" column switched off. A band here is a target ± tolerance typed on the
    row, so a second Min/Max pair would only contradict it; and every PV this tab can
    read is an archiver channel, so a column saying so on every row says nothing.
  * **One registry.** A PV added or renamed in any tab is the same PV in the others —
    `is_t.PV_CUSTOM_CHANNELS` / `PV_LABELS` / `PV_CUSTOM_UNITS`, saved by
    `pv_registry_save`. This tab's own file keeps only what is its own: the band, and
    which PVs the search filters on.
  * **The eye decides which PVs have a row.** Open eye → a row under the button.
    Shut → still picked, still in the registry, but no row, so it is neither filtered
    on nor shown.
  * **A band survives a trip through the picker.** Opening it to add one PV must not
    retype the target of the others.
  * **A formula is a column like any other.** The search reads the PVs it is computed
    from by itself, so they need no row (26.10.2026 — it used to be left out).

Offscreen, and the dialog is never executed — its answer is stubbed, because what is
under test is what this tab DOES with the answer:

    python testing/test_sf_pv_picker.py
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication, QDialog, QLabel   # noqa: E402

import sf_t                                                    # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


class FakeDialog:
    """Stands in for PvConfigDialog: it is handed the same arguments and gives back
    the same seven answers. Nothing is shown."""
    seen: dict = {}

    def __init__(self, enabled, custom, derived, labels, hidden=None, units=None,
                 limits=None, parent=None, show_limits=True, show_kind=True):
        FakeDialog.seen = {"enabled": list(enabled),
                           "hidden": set(hidden or ()),
                           "show_limits": show_limits, "show_kind": show_kind}
        self._custom = dict(custom)
        self._labels = dict(labels or {})
        self._units = dict(units or {})
        self._limits = dict(limits or {})

    # what the test tells it to answer with
    answer: dict = {}

    def exec(self):
        return QDialog.DialogCode.Accepted

    def selected_names(self):
        return list(self.answer.get("selected", []))

    def hidden_names(self):
        return list(self.answer.get("hidden", []))

    def custom_channels(self):
        d = dict(self._custom)
        d.update(self.answer.get("custom", {}))
        return d

    def derived_defs(self):
        return list(self.answer.get("derived", []))

    def labels(self):
        d = dict(self._labels)
        d.update(self.answer.get("labels", {}))
        return d

    def custom_units(self):
        d = dict(self._units)
        d.update(self.answer.get("units", {}))
        return d

    def limits(self):
        return dict(self._limits)


def with_answer(w, sl, **answer):
    FakeDialog.answer = answer
    real = sl.PvConfigDialog
    sl.PvConfigDialog = FakeDialog
    try:
        w._open_pv_config()
    finally:
        sl.PvConfigDialog = real


def cols(w):
    return [c["col"] for c in w._pv_cfg]


# ── the checks ────────────────────────────────────────────────────────────────
def test_panel_has_a_button_not_a_search_box(w):
    print("\nThe panel: a button and the list, no search box")
    check("the Configure PVs button is there", hasattr(w, "_btn_pv_cfg"))
    check("and says so", w._btn_pv_cfg.text().endswith("Configure PVs"),
          w._btn_pv_cfg.text())
    check("the old search box is gone", not hasattr(w, "_pv_search"))
    check("and its floating list with it", not hasattr(w, "_pv_dropdown"))
    check("the group is spelled PVs, not PVS",
          "PVs" in w._sections["pv"]._header.text(),
          w._sections["pv"]._header.text())


def test_dialog_is_asked_as_a_picker(w, sl):
    print("\nThe picker is opened without the two columns this tab has no use for")
    with_answer(w, sl, selected=["SBW4"], hidden=[])
    check("Min/Max are off", FakeDialog.seen["show_limits"] is False)
    check("and 'What it is' too", FakeDialog.seen["show_kind"] is False)


def test_picked_pvs_become_rows(w, sl):
    print("\nWhat comes back becomes the rows under the button")
    with_answer(w, sl, selected=["SBW4", "PTM1", "PCM2"], hidden=[])
    check("one row per picked PV", cols(w) == ["sbw4", "ptm1", "pcm2"], str(cols(w)))
    check("and the panel drew them", len(w._pv_rows) == 3, str(len(w._pv_rows)))


def test_the_eye_decides_who_gets_a_row(w, sl):
    print("\nA shut eye keeps the PV picked but gives it no row")
    with_answer(w, sl, selected=["SBW4", "PTM1", "PCM2"], hidden=["PTM1"])
    check("the hidden PV has no row", cols(w) == ["sbw4", "pcm2"], str(cols(w)))
    check("but is still picked", "PTM1" in w._pv_registered, str(w._pv_registered))
    # Re-opening must offer it as picked-and-hidden, not as gone.
    with_answer(w, sl, selected=["SBW4", "PTM1", "PCM2"], hidden=[])
    check("re-opening offers it back", "PTM1" in FakeDialog.seen["enabled"],
          str(FakeDialog.seen["enabled"]))
    check("as a hidden one", "PTM1" in FakeDialog.seen["hidden"],
          str(FakeDialog.seen["hidden"]))
    check("and opening its eye gives it its row back",
          cols(w) == ["sbw4", "ptm1", "pcm2"], str(cols(w)))


def test_the_band_survives_the_trip(w, sl):
    print("\nA target typed on a row is not retyped by a trip through the picker")
    with_answer(w, sl, selected=["SBW4"], hidden=[])
    w._pv_cfg[0]["target"] = 20.0
    w._pv_cfg[0]["tol"] = 0.5
    w._pv_cfg[0]["filter"] = False
    w._pv_rows = []                       # read _pv_cfg as it stands
    w._sync_pv_cfg_from_rows = lambda: None
    with_answer(w, sl, selected=["SBW4", "PTM1"], hidden=[])
    kept = next(c for c in w._pv_cfg if c["col"] == "sbw4")
    check("its target is untouched", kept["target"] == 20.0, str(kept["target"]))
    check("its tolerance too", kept["tol"] == 0.5, str(kept["tol"]))
    check("and so is its tick", kept["filter"] is False, str(kept["filter"]))
    fresh = next(c for c in w._pv_cfg if c["col"] == "ptm1")
    check("a PV added now starts as a filter PV", fresh["filter"] is True)


def test_names_and_units_are_shared(w, sl):
    print("\nA name or a unit typed in the picker is the shared one")
    with_answer(w, sl, selected=["SBW4", "MY-OWN:Chan"], hidden=[],
                custom={"MY-OWN:Chan": "MY-OWN:Chan"},
                labels={"MY-OWN:Chan": "My own"},
                units={"MY-OWN:Chan": "mrad"})
    check("the added channel got a row", "MY-OWN:Chan" in cols(w), str(cols(w)))
    check("it is read under its typed name",
          w._col_short("MY-OWN:Chan") == "My own", w._col_short("MY-OWN:Chan"))
    check("with the typed unit", w._col_unit("MY-OWN:Chan") == "mrad",
          w._col_unit("MY-OWN:Chan"))
    check("and the shared registry holds both",
          sl.PV_LABELS.get("MY-OWN:Chan") == "My own"
          and sl.PV_CUSTOM_UNITS.get("MY-OWN:Chan") == "mrad")
    check("a preset keeps its own label when nobody renamed it",
          w._col_short("sbw4") == "SBW4", w._col_short("sbw4"))


def test_a_formula_gets_a_row_without_its_source(w, sl):
    print("\nA formula is searchable, and its source PV needs no row of its own")
    # Asked for on 26.10.2026: "program by neměl potřebovat tam mít v tom seznamu tu
    # závislost, ze které se to počítá". Compressed SBW4 hides its source on purpose.
    with_answer(w, sl, selected=["SBW4", "Ratio"], hidden=["SBW4"],
                derived=[{"name": "Ratio", "expr": "A/2", "unit": "J",
                          "bindings": {"A": "SBW4"}}])
    check("the formula got a row", cols(w) == ["Ratio"], str(cols(w)))
    check("with the formula's unit", w._col_unit("Ratio") == "J", w._col_unit("Ratio"))
    check("its tooltip names the channel it reads",
          sl.PV_CHANNEL_MAP["SBW4"] in w._col_channel("Ratio"), w._col_channel("Ratio"))
    check("the search warms the source channel, not the formula's name",
          sf_t._col_read_channels("Ratio") == [sl.PV_CHANNEL_MAP["SBW4"]],
          str(sf_t._col_read_channels("Ratio")))

    # One day computed from stubbed archiver samples: every source sample → one value.
    real_get_day = sf_t.cpva.get_day
    base = 1_790_000_000 * 10**9
    sf_t.cpva.get_day = lambda ch, dk, **kw: SimpleNamespace(
        status="ok", age_s=0.0,
        samples=[(base, 20.0), (base + 5 * 10**9, 21.0)])
    try:
        logged: list = []
        rows, meta = sf_t._formula_rows_for_day("Ratio", "2026-10-26", None,
                                                logged.append)
    finally:
        sf_t.cpva.get_day = real_get_day
    vals = [float(r["Ratio"]) for r in rows]
    check("each sample is computed", vals == [10.0, 10.5], str(vals))
    check("and the day reads as data", meta["status"] == "ok", str(meta))
    sl.PV_DERIVED[:] = []


def test_picking_a_hidden_pv_again_gives_it_a_row(w, sl):
    print("\nA PV already in the list with its eye shut, picked again, gets its row")
    # Seen on 26.10.2026: GDD was in the shared registry and hidden in this tab.
    # Searching for it and clicking it only said "already in the list", the eye
    # stayed shut, and OK left the panel without it. The REAL dialog is used here,
    # because the bug was in the dialog itself; only exec() is stubbed.
    chan = "L3-TEST-AOD03-002:Order2_RB"
    sl.PV_CUSTOM_CHANNELS[chan] = chan
    w._pv_cfg = [{"col": "sbw4", "target": 10.0, "tol": 0.0, "filter": True}]
    w._pv_rows = []
    w._pv_registered = ["SBW4", chan]
    w._pv_hidden = {chan}
    real_exec = sl.PvConfigDialog.exec

    def fake_exec(dlg):
        dlg._add_channel(chan)
        dlg._on_accept()
        return dlg.result()
    sl.PvConfigDialog.exec = fake_exec
    try:
        w._open_pv_config()
    finally:
        sl.PvConfigDialog.exec = real_exec
    check("it got its row", chan in cols(w), str(cols(w)))
    check("and is no longer hidden", chan not in w._pv_hidden, str(w._pv_hidden))
    sl.PV_CUSTOM_CHANNELS.pop(chan, None)


def test_the_dialog_drops_the_two_columns(app, sl):
    print("\nThe real dialog, built as a picker: the two columns are simply not there")
    dlg = sl.PvConfigDialog(["SBW4"], {}, [], {}, hidden=set(), units={}, limits={},
                            show_limits=False, show_kind=False)
    titles = {w.text() for w in dlg.findChildren(QLabel)}
    check("no Min column", "Min" not in titles)
    check("no Max column", "Max" not in titles)
    check("no 'What it is' column", "What it is" not in titles)
    check("the PV column is still there", "PV" in titles)
    check("and so is Displayed name", "Displayed name" in titles)
    dlg.deleteLater()

    full = sl.PvConfigDialog(["SBW4"], {}, [], {}, hidden=set(), units={}, limits={})
    ftitles = {w.text() for w in full.findChildren(QLabel)}
    check("the Slider's own picker keeps them",
          {"Min", "Max", "What it is"} <= ftitles)
    full.deleteLater()


def main():
    import tempfile
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
    sl = sf_t._get_slider_module()

    # Both stores are moved into a temp folder BEFORE anything is built: this test
    # adds PVs and renames them, and the operator's own registry and panel state are
    # not something a test run may rewrite.
    tmp = Path(tempfile.mkdtemp(prefix="sf_pv_picker_"))
    sl.PV_REGISTRY_PATH = tmp / "pv_registry.json"
    sf_t.ShotFinderWidget._UI_STATE_PATH = tmp / "shotfinder_ui_state.json"
    sl.PV_CUSTOM_CHANNELS.clear()
    sl.PV_LABELS.clear()
    sl.PV_CUSTOM_UNITS.clear()
    sl.PV_LIMITS.clear()
    sl.PV_DERIVED[:] = []
    print(f"state in {tmp}")

    w = sf_t.ShotFinderWidget()

    test_panel_has_a_button_not_a_search_box(w)
    test_dialog_is_asked_as_a_picker(w, sl)
    test_picked_pvs_become_rows(w, sl)
    test_the_eye_decides_who_gets_a_row(w, sl)
    test_the_band_survives_the_trip(w, sl)
    test_names_and_units_are_shared(w, sl)
    test_a_formula_gets_a_row_without_its_source(w, sl)
    test_picking_a_hidden_pv_again_gives_it_a_row(w, sl)
    test_the_dialog_drops_the_two_columns(app, sl)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
