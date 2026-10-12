"""
Renders the graph tool strip, the PV table, a PV Time Plot condition row and
the loupe to PNGs for a visual check. Nothing appears on screen: run with
QT_QPA_PLATFORM=minimal:enable_fonts (real fonts, no windows).

    python testing/probe_graph_strip_look.py <out_dir>
"""
import os
import sys
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import test_smoke as ts                     # noqa: E402

A, B = "L3-TEST-A:Energy", "L3-TEST-B:Energy"


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    w = ts._loaded_widget((A, B))
    w.resize(1400, 900)
    w.show()
    ts._app.processEvents()
    w._plot_graph()
    ts._app.processEvents()

    w._graph_strip.grab().save(str(out / "strip.png"))
    w._gstrip_btns["zoom"].setChecked(True)
    w._graph_strip.grab().save(str(out / "strip_zoom_on.png"))
    w._axis_tv.grab().save(str(out / "pv_table.png"))

    ed = w._pv_time_cond_editor
    ed.add_row({"pv": A, "enabled": True, "kind": "between", "a": 1.0, "b": 5.0},
               announce=False)
    ed.add_row({"pv": B, "enabled": True, "kind": "above", "a": 2.0},
               announce=False)
    ed.resize(1100, ed.sizeHint().height())
    ts._app.processEvents()
    ed.grab().save(str(out / "conditions.png"))

    w._set_loupe_on(True)
    lo, hi = w._graph_axes[0].get_xlim()
    x = lo + 0.5 * (hi - lo)
    ax_b, src_b = w._graph_axes[1], w._graph_lines[1][-1]
    xs, ys = src_b.get_xdata(), src_b.get_ydata()
    i = max(0, int((xs <= x).sum()) - 1)
    _xp, y_px = ax_b.transData.transform((x, float(ys[i])))
    w._loupe.update_at(x, w._graph_axes, w._graph_lines, 5, None,
                       y_px=y_px, names=["A - Energy", "B - Energy"])
    ts._app.processEvents()
    w._loupe.grab().save(str(out / "loupe.png"))
    w.close()


if __name__ == "__main__":
    ts._install_dialog_mocks()
    main(sys.argv[1] if len(sys.argv) > 1 else str(_HERE / "_look"))
