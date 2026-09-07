"""Time-axis stamps must sit centred under their own tick mark.

Reproduces the bug where a stamp was printed noticeably left of its tick:
_apply_x_ticks pins the OUTER two stamps inside the plot (left/right aligned) so
they do not hang off the figure, but matplotlib recycles the label objects by
position. A range that produced 6 stamps left index 5 right-aligned; the next
range produced 7, so index 5 was an interior stamp still glued to its right
edge. Only the two outermost stamps may be off-centre, and only for the tick set
currently on the axis.

Run:  python test_x_tick_alignment.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator


def _stamp(ax, positions, reset_first):
    """Install `positions` as the major ticks, the way _apply_x_ticks does."""
    ax.xaxis.set_major_locator(FixedLocator(positions))
    labels = ax.get_xticklabels()
    if reset_first:
        for t in labels:
            t.set_horizontalalignment("center")
    if len(labels) >= 2:
        labels[0].set_horizontalalignment("left")
        labels[-1].set_horizontalalignment("right")
    return [t.get_horizontalalignment() for t in ax.get_xticklabels()]


def _run(reset_first):
    fig, ax = plt.subplots()
    ax.set_xlim(0, 60)
    # First range: 6 stamps (window edges + 10-minute steps), like 14:25…15:25.
    _stamp(ax, [0, 15, 25, 35, 45, 60], reset_first)
    # A live scroll later: the same window shifted by a minute now fits 7.
    got = _stamp(ax, [1, 15, 25, 35, 45, 55, 60], reset_first)
    plt.close(fig)
    return got


def main():
    want = ["left", "center", "center", "center", "center", "center", "right"]
    before = _run(reset_first=False)
    after = _run(reset_first=True)

    print("without the reset:", before)
    print("with the reset:   ", after)
    print("expected:         ", want)

    if before == want:
        print("INCONCLUSIVE - matplotlib no longer recycles the labels, "
              "the bug cannot be reproduced here")
        return 0
    bad = [i for i, (g, w) in enumerate(zip(after, want)) if g != w]
    if bad:
        print("FAIL - stamp(s) %s not centred on their tick" % bad)
        return 1
    print("PASS - only the two outer stamps are pinned; the interior one that "
          "used to keep a stale right alignment (index 5) is centred again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
