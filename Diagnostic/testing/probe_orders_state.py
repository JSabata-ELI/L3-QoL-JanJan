"""Print what the portal says about MY OWN lunch orders. Read-only.

`nacti-vse` answers with two halves: `listky` (the menu, which okbase_menu
already parses) and `objednavky` (what this person has ordered). Only the first
half was ever looked at. Ordering has to send the whole week's desired state
back, so this prints the second half raw, next to the item ids and course codes
of the menu it refers to.

Usage:
    python probe_orders_state.py [YYYY-MM-DD]     any day in the wanted week
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import okbase_menu as om


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def main(argv: list[str]) -> int:
    want = date.fromisoformat(argv[0]) if argv else date.today()
    monday = week_monday(want)

    settings = om._cli_settings()
    session, base, timeout, err = om.open_session(settings)
    if session is None:
        print(f"not signed in: {err}")
        return 1

    body = om.retarget_filter(
        dict(settings.get("okbase_filter") or {}),
        monday - timedelta(days=om.FETCH_DAYS_BACK),
        monday + timedelta(days=om.FETCH_DAYS_AHEAD),
        monday) if settings.get("okbase_filter") else om.filter_candidates(
            monday - timedelta(days=om.FETCH_DAYS_BACK),
            monday + timedelta(days=om.FETCH_DAYS_AHEAD),
            settings.get("okbase_canteen_id"),
            settings.get("okbase_user_id"), monday)[0]

    url = f"{base}/rest/stravovani/objednavky/nacti-vse"
    r = session.post(url, json=body, timeout=timeout)
    print(f"HTTP {r.status_code}   week of {monday.isoformat()}")
    if not (200 <= r.status_code < 300):
        print((r.text or "")[:400])
        return 1
    payload = r.json()

    print(f"top-level keys: {sorted(payload)}")
    print("")

    week = {(monday + timedelta(days=i)).isoformat() for i in range(7)}

    orders = payload.get("objednavky") or {}
    print(f"=== objednavky: {len(orders)} days answered, "
          f"{min(orders, default='-')} … {max(orders, default='-')} ===")
    for day in sorted(orders):
        for order in (orders[day] or []):
            items = [p.get("id") for p in (order.get("polozky") or [])]
            print(f"{day}  order#{order.get('id')}  {order.get('stav')}"
                  f"  zadna={order.get('zadna')}  items={items}"
                  f"  burza(offer={order.get('lzeNabidnoutNaBurze')},"
                  f"take={order.get('lzeStahnoutZBurzy')})")
    print("")
    print("=== this week's orders, raw ===")
    print(json.dumps({d: v for d, v in orders.items() if d in week},
                     indent=2, ensure_ascii=False)[:6000])
    print("")

    print("=== listky: item id -> course / name, per day ===")
    tickets = payload.get("listky") or {}
    for day in sorted(d for d in tickets if d in week):
        node = tickets[day] or {}
        extra = {k: v for k, v in node.items()
                 if k not in ("polozky",) and not isinstance(v, (dict, list))}
        print(f"{day}  {extra}")
        for item in (node.get("polozky") or []):
            meal = item.get("jidlo") or {}
            name = str(meal.get("nazev") or "")[:58]
            print(f"    item id {item.get('id')}"
                  f"  order#{item.get('poradi')}"
                  f"  {str(meal.get('typ') or ''):14}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
