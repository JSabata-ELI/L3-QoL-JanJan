"""Order or cancel a real lunch from the shell, to prove the write path works.

This is the one test that touches the live portal and really changes an order,
so it prints the day's state before and after and never guesses: everything it
does comes from `okbase_menu.change_order`, the same function `/food order`
calls.

    python live_order.py --show friday          what is ordered, and the list
    python live_order.py friday main 1          order main course 1
    python live_order.py friday soup 2 main 1   a soup as well
    python live_order.py --cancel friday        clear that day

The day words are the ones `/food` takes: a weekday, `today`, `tomorrow`, or a
date (`11.9.`, `2026-09-11`). Close Diagnostic first if it is open — both would
be writing the same sign-in file.

No ordering password is asked for here, unlike `/food order`: this runs as the
Windows account whose DPAPI sign-in decrypts, which is already a stronger claim
than a word typed into a chat room.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The replies carry the course emoji and Czech names, and a Windows console is
# on cp1250 — without this the script dies half way through printing a menu.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass

import okbase_menu as om


def show(settings: dict, day: date) -> int:
    session, base, timeout, err = om.open_session(settings)
    if session is None:
        print(f"not signed in: {err}")
        return 1
    monday = om.week_monday(day)
    payload, _body, err = om.fetch_raw(
        session, monday - om.timedelta(days=om.FETCH_DAYS_BACK),
        monday + om.timedelta(days=om.FETCH_DAYS_AHEAD), base, timeout,
        settings.get("okbase_canteen_id") or None,
        settings.get("okbase_filter") or None,
        settings.get("okbase_user_id") or None, week_of=monday)
    if payload is None:
        print(f"could not read the portal: {err}")
        return 1

    iso = day.isoformat()
    meals = om.parse_menu(payload).get(iso) or []
    state = om.parse_day_states(payload).get(iso, "unknown")
    order = om.parse_orders(payload).get(iso)

    print(f"{iso}  ({state} for orders)")
    if not meals:
        print("  no menu that day")
        return 0
    for line in om.render_meals(meals, "cs"):
        print(f"  {line}")
    print("")
    if order and order.ordered:
        by_id = {m.item_id: m for m in meals}
        names = [by_id[i].titles("cs")[0] if i in by_id else str(i)
                 for i in order.item_ids]
        print(f"  ordered ({order.state}): " + ", ".join(names))
    else:
        print("  nothing ordered")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    args = [a for a in argv]
    only_show = "--show" in args
    cancel = "--cancel" in args
    args = [a for a in args if not a.startswith("--")]

    req = om.parse_food_args(
        ("cancel " if cancel else "order ") + " ".join(args))
    if req.error:
        print(f"⚠ {req.error}")
        return 2
    day = req.day
    if day is None:
        print("name a day: friday, tomorrow, 11.9. …")
        return 2

    settings = om._cli_settings()
    if only_show:
        return show(settings, day)

    print("before:")
    show(settings, day)
    print("")

    out: dict = {}
    outcome = om.change_order(settings, day, req.picks, cancel,
                              session_out=out)
    print(om.render_order_outcome(outcome, cancel, "cs"))
    print("")
    print("after:")
    show(settings, day)
    return 1 if outcome.error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
