"""Ordering lunch: the words, the ids and the save body. No network.

Pinned against the real `nacti-vse` answer and the real `uloz` request, both
captured live on 2026-09-09 (see STRUCTURE.md). The save body is the dangerous
half of this feature — it rewrites a whole week — so every test here is about
what exactly goes into it.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import okbase_menu as om

# Spending a one-time code WRITES the Windows account's real okbase.json, and a
# test must never touch the operator's own settings. Stubbed here, at the top,
# for the whole file — not just around the part that spends: the point is that
# no future check added below can reach that file by accident.
_FAKE_DISK: dict = {}
om.load_user_settings = lambda: dict(_FAKE_DISK)
om.save_user_settings = lambda values: (_FAKE_DISK.update(values), "")[1]

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}\n       got  {got!r}\n       want {want!r}")


# --------------------------------------------------------------------------- #
# A cut-down copy of the real answer: Thursday open, Friday open, Wednesday
# closed and already collected.
# --------------------------------------------------------------------------- #

def _item(item_id: int, order: int, code: str, name: str, price: float) -> dict:
    return {"id": item_id, "poradi": order,
            "jidlo": {"nazev": name, "typ": code, "popis": "9",
                      "aktualniCenaDotovana": price,
                      "aktualniCenaPlna": price * 3,
                      "kategorie": [{"textCs": "obed",
                                     "organizace": {"nazev": "ELI ERIC"}}]}}


PAYLOAD = {
    "listky": {
        "2026-09-09": {
            "id": 572625, "stav": "UZAVRENY", "jidelnaId": 1,
            "datumVydeje": "2026-09-09", "datumUzavreni": "2026-09-09T10:00:00.000",
            "polozky": [
                _item(572627, 2, "POLEVKA", "Batátový krém / Sweet potato cream", 22),
                _item(572628, 3, "HLAVNI_JIDLO", "Pečená kachna / Roast duck", 95),
            ]},
        "2026-09-10": {
            "id": 572632, "stav": "ZVEREJNENY", "jidelnaId": 1,
            "datumVydeje": "2026-09-10", "datumUzavreni": None,
            "polozky": [
                _item(572633, 1, "POLEVKA", "Hrachová s pórkem/ Pea soup", 22),
                _item(572634, 2, "POLEVKA", "Lámanková s vejcem", 22),
                _item(572635, 3, "HLAVNI_JIDLO", "Plněný paprikový lusk", 95),
                _item(572636, 4, "HLAVNI_JIDLO", "Zapečená treska / Baked cod", 95),
            ]},
        "2026-09-11": {
            "id": 572639, "stav": "ZVEREJNENY", "jidelnaId": 1,
            "datumVydeje": "2026-09-11", "datumUzavreni": None,
            "polozky": [
                _item(572640, 1, "POLEVKA", "Rajčatová s cizrnou / Tomato soup", 22),
                _item(572641, 2, "POLEVKA", "Cibulačka s krutóny / Onion soup", 22),
                _item(572642, 3, "HLAVNI_JIDLO",
                      "Sekaná s bramborovou kaší/ Meat loaf with mash", 95),
                _item(572643, 4, "HLAVNI_JIDLO", "Kuřecí nudličky", 95),
            ]},
    },
    "objednavky": {
        "2026-09-07": [],
        "2026-09-09": [{"id": 573772, "datumVydeje": "2026-09-09",
                        "stav": "ODEBRANO", "zadna": False,
                        "polozky": [{"id": 572627, "jidlo": None},
                                    {"id": 572628, "jidlo": None}]}],
        "2026-09-10": [{"id": 575186, "datumVydeje": "2026-09-10",
                        "stav": "OBJEDNANO", "zadna": False,
                        "polozky": [{"id": 572633, "jidlo": None},
                                    {"id": 572636, "jidlo": None}]}],
        "2026-09-11": [],
    },
}

MONDAY = date(2026, 9, 7)


# --------------------------------------------------------------------------- #
print("reading the answer")

menu = om.parse_menu(PAYLOAD)
friday = menu["2026-09-11"]
check("Friday has 4 meals", len(friday), 4)
check("the item id is kept", [m.item_id for m in friday],
      [572640, 572641, 572642, 572643])
check("the course code is kept", [m.code for m in friday],
      ["POLEVKA", "POLEVKA", "HLAVNI_JIDLO", "HLAVNI_JIDLO"])
check("the human course still reads as words", friday[2].kind, "main course")
check("the subsidised price is the one shown", friday[2].price, "95 Kč")
check("the institute is not on the menu",
      any("ELI" in m.name for m in friday), False)

states = om.parse_day_states(PAYLOAD)
check("Wednesday is closed", states["2026-09-09"], om.DAY_CLOSED)
check("Friday is open", states["2026-09-11"], om.DAY_OPEN)

orders = om.parse_orders(PAYLOAD)
check("Thursday is ordered", orders["2026-09-10"].ordered, True)
check("Thursday's items", orders["2026-09-10"].item_ids, (572633, 572636))
check("Friday is not ordered", orders["2026-09-11"].ordered, False)
check("Monday has no order at all", orders["2026-09-07"].ordered, False)


# --------------------------------------------------------------------------- #
print("")
print("the words after /food order")


def req(line: str):
    return om.parse_food_args(line, today=date(2026, 9, 9))


check("a bare number is the main course", req("order friday 1").picks,
      (("HLAVNI_JIDLO", 1),))
check("the day is read", req("order friday 1").day, date(2026, 9, 11))
check("soup and main together", req("order friday soup 2 main 1").picks,
      (("HLAVNI_JIDLO", 1), ("POLEVKA", 2)))
check("the day may come last", req("order soup 2 friday").picks,
      (("POLEVKA", 2),))
check("the day may come last (day)", req("order soup 2 friday").day,
      date(2026, 9, 11))
check("Czech words work", req("order pátek hlavní 2").picks,
      (("HLAVNI_JIDLO", 2),))
check("a number glued on works", req("order friday p2 h1").picks,
      (("HLAVNI_JIDLO", 1), ("POLEVKA", 2)))
check("next week", req("order next friday 1").day, date(2026, 9, 18))
check("a date works", req("order 11.9. 1").day, date(2026, 9, 11))
check("cancel takes a day", req("cancel friday").mode, "cancel")

# The word order that actually got typed at the live bot and quietly printed
# the menu instead: the day first, the verb after it.
check("the day may come before the verb", req("friday cancel").mode, "cancel")
check("and the day still lands", req("friday cancel").day, date(2026, 9, 11))
check("same for ordering", req("friday order 1").picks,
      (("HLAVNI_JIDLO", 1),))
check("and its day", req("friday order 1").day, date(2026, 9, 11))
check("with the code anywhere too",
      req("friday cancel pin:bakoli").password, "bakoli")
check("Czech verbs work", req("pátek zrušit").mode, "cancel")
check("and Czech ordering", req("pátek objednej hlavní 2").picks,
      (("HLAVNI_JIDLO", 2),))
check("the listing word is found anywhere too", req("cz orders").mode,
      "orders")

# The line that was actually typed at the live bot: a meal, a code, and no verb
# at all. Nobody types a one-time code to read a menu.
r = req("friday main 1 pin:bakoli")
check("a code and a meal make it an order, verb or no verb", r.mode, "order")
check("with the meal", r.picks, (("HLAVNI_JIDLO", 1),))
check("and the day", r.day, date(2026, 9, 11))
check("and the code", r.password, "bakoli")
check("a bare number counts too",
      req("friday 2 pin:bakoli").picks, (("HLAVNI_JIDLO", 2),))
check("without a code it is still just the menu",
      req("friday main 1").mode, "day")
check("and a code with no meal is still just the menu",
      req("friday pin:bakoli").mode, "day")

print("")
print("swapping an order that is already there")

check("plain order does not ask to replace", req("order friday 1").replace,
      False)
check("`change` does", req("change friday 1").replace, True)
check("so does `instead`", req("instead friday 1").replace, True)
check("and Czech", req("pátek změnit 3").replace, True)
check("a replace is still an order", req("change friday 1").mode, "order")
check("with its meal", req("change friday 3").picks, (("HLAVNI_JIDLO", 3),))
check("cancel is not a replace", req("cancel friday").replace, False)
check("cancel's day", req("cancel friday").day, date(2026, 9, 11))
check("cancel refuses a meal", bool(req("cancel friday 1").error), True)
check("two mains is a typo", bool(req("order friday main 1 main 2").error), True)
check("the same main twice is fine",
      req("order friday main 1 main 1").picks, (("HLAVNI_JIDLO", 1),))
check("no meal named, no error", req("order friday").error, "")
check("no meal named, no picks", req("order friday").picks, ())
check("/food orders is a reading command", req("orders").mode, "orders")
check("nonsense is named", "banana" in req("order banana").error, True)
check("the language word still works anywhere", req("order friday 1 cz").lang, "cs")


# --------------------------------------------------------------------------- #
print("")
print("numbers to item ids")

ids, err = om.resolve_picks(friday, (("HLAVNI_JIDLO", 1),))
check("main 1 is the meat loaf", ids, {"HLAVNI_JIDLO": 572642})
check("no complaint", err, "")
ids, err = om.resolve_picks(friday, (("POLEVKA", 2), ("HLAVNI_JIDLO", 1)))
check("soup 2 and main 1", ids,
      {"POLEVKA": 572641, "HLAVNI_JIDLO": 572642})
ids, err = om.resolve_picks(friday, (("HLAVNI_JIDLO", 9),))
check("a number past the end is refused", ids, {})
check("and says how many there are", "has 2" in err, True)
ids, err = om.resolve_picks(friday, (("POLEVKA", 0),))
check("0 means that course off", ids, {"POLEVKA": None})
check("and is not an error", err, "")


# --------------------------------------------------------------------------- #
print("")
print("the save body")

desired, err = om.desired_week(menu, orders, MONDAY)
check("no complaint building it", err, "")
check("only the days with a menu are sent", sorted(desired),
      ["2026-09-09", "2026-09-10", "2026-09-11"])
check("a closed collected day is sent back as it stands",
      desired["2026-09-09"],
      [{"polozkyIdMap": {"POLEVKA": 572627, "HLAVNI_JIDLO": 572628},
        "zadna": False}])
check("a day with no order says 'none'", desired["2026-09-11"],
      [{"zadna": True}])

# The one change, applied the way change_order applies it.
codes = {m.item_id: m.code for m in friday}
entry, err = om._order_entry((572642,), codes, existed=False)
check("a brand-new order carries objednavkaId 0", entry,
      {"polozkyIdMap": {"HLAVNI_JIDLO": 572642}, "objednavkaId": 0})
entry, err = om._order_entry((572633, 572636),
                             {m.item_id: m.code for m in menu["2026-09-10"]},
                             existed=True)
check("an existing order carries zadna false", entry,
      {"polozkyIdMap": {"POLEVKA": 572633, "HLAVNI_JIDLO": 572636},
       "zadna": False})
entry, err = om._order_entry((), codes, existed=True)
check("cancelling is zadna true", entry, {"zadna": True})
entry, err = om._order_entry((999999,), codes, existed=False)
check("an unknown course is refused, not guessed", entry, {})
check("and says why", "which course" in err, True)

# A meal in the current order that the menu does not explain must stop the
# whole save: filing it under no course would read as "that meal is gone".
hurt = dict(orders)
hurt["2026-09-10"] = om.Order(day="2026-09-10", order_id=1, state="OBJEDNANO",
                              item_ids=(572633, 111111), none_ordered=False)
desired, err = om.desired_week(menu, hurt, MONDAY)
check("an unexplained meal stops the save", desired, {})
check("and says so", bool(err), True)


# --------------------------------------------------------------------------- #
print("")
print("the cache carries it")

cache = om.build_cache(menu, filter_used={"userId": 1}, states=states,
                       orders=orders)
check("states are saved", cache["states"]["2026-09-11"], om.DAY_OPEN)
check("only real orders are saved — a day with 'zadna' is not one",
      sorted(cache["orders"]), ["2026-09-09", "2026-09-10"])
back = om.cache_days(cache)["2026-09-11"]
check("the ids survive a round trip", [m.item_id for m in back],
      [572640, 572641, 572642, 572643])
check("the codes survive a round trip", back[2].code, "HLAVNI_JIDLO")

old = {"version": 1, "fetched": "2026-09-01T08:00:00",
       "days": {"2026-09-11": [{"name": "Sekaná", "price": "95 Kč",
                                "kind": "main course"}]}}
check("a cache written before ordering existed still reads",
      om.cache_days(old)["2026-09-11"][0].item_id, None)


# --------------------------------------------------------------------------- #
print("")
print("what it says afterwards")

out = om.OrderOutcome(day="2026-09-11", ordered=(friday[2],))
text = om.render_order_outcome(out, lang="cs")
check("the reply names the meal", "Sekaná" in text, True)
out = om.OrderOutcome(day="2026-09-11", was=(friday[2],))
check("cancelling says what was there",
      "was Sekaná" in om.render_order_outcome(out, cancelled=True, lang="cs"),
      True)
out = om.OrderOutcome(day="2026-09-11", error="2026-09-11 is already closed")
check("a refusal is quoted, not softened",
      "already closed" in om.render_order_outcome(out), True)

out = om.OrderOutcome(day="2026-09-11", already=True, was=(friday[2],))
text = om.render_order_outcome(out, lang="cs")
check("already booked says so", "already what you have ordered" in text, True)
check("and names what is on it", "Sekaná" in text, True)
check("and says no code was used", "no code was used" in text, True)
check("and how to swap it", "/food order 11.09. 2 pin:" in text, True)
check("and how to drop the day", "/food cancel 11.09." in text, True)

text = om.render_orders(cache, lang="cs")
check("/food orders lists Thursday", "Zapečená treska" in text, True)
check("/food orders does not invent Friday", "Sekaná" in text, False)


# --------------------------------------------------------------------------- #
print("")
print("the menu marks what is ordered")

M = om.ORDERED_MARK

lines = om.render_meals(menu["2026-09-10"], "cs", [572633, 572636])
body = "\n".join(lines)
check("the ordered soup is marked", f"1) {M} **Hrachová" in body, True)
check("the ordered main is marked", f"2) {M} **Zapečená treska" in body, True)
check("the others are not", "2) **Lámanková" in body, True)
check("exactly two marks", body.count(M), 2)
check("nothing marked when nothing is ordered",
      M in "\n".join(om.render_meals(menu["2026-09-10"], "cs")), False)

# The mark is matched on the id, never on the name: the same dish can appear on
# two days, and only one of them may be ordered.
twice = [om.Meal(name="Sekaná", kind="main course", item_id=1,
                 code="HLAVNI_JIDLO"),
         om.Meal(name="Sekaná", kind="main course", item_id=2,
                 code="HLAVNI_JIDLO")]
body = "\n".join(om.render_meals(twice, "cs", [2]))
check("the same name twice marks only the ordered one", body.count(M), 1)
check("and it is the second", f"2) {M} **Sekaná" in body, True)

text = om.render_day(cache, date(2026, 9, 10), lang="cs")
check("a day reply carries the marks", text.count(M), 2)
check("and no note is needed", "too old" in text, False)
text = om.render_day(cache, date(2026, 9, 11), lang="cs")
check("a day with nothing ordered has no marks", M in text, False)

week = om.render_week(cache, MONDAY, lang="cs")
# Wednesday and Thursday are both ordered in this cache — two meals each — and
# a day already collected is marked like any other: it IS what was ordered.
check("the week reply carries them too", week.count(M), 4)
check("Wednesday's is marked as well",
      f"1) {M} **Batátový" in week, True)

# A cache written before the item ids were kept cannot mark anything, and must
# say so rather than read as "nothing ordered".
old = dict(cache)
old["days"] = {d: [{k: v for k, v in m.items() if k != "item_id"} for m in ms]
               for d, ms in cache["days"].items()}
text = om.render_day(old, date(2026, 9, 10), lang="cs")
check("an old saved copy admits it cannot say which meal",
      "too old to say which meal" in text, True)
check("and does not pretend nothing is ordered", M in text, True)

# An order for a meal the saved menu does not hold is named, not hidden.
odd = dict(cache)
odd["orders"] = {"2026-09-10": {"items": [572633, 999999], "state": "OBJEDNANO"}}
text = om.render_day(odd, date(2026, 9, 10), lang="cs")
check("a meal missing from the saved menu is counted",
      "1 ordered item(s) are not on this saved menu" in text, True)
check("while the one that is there is still marked",
      f"1) {M} **Hrachová" in text, True)


# --------------------------------------------------------------------------- #
print("")
print("who may order")

WORDS, BLOB = om.new_order_codes(4)
OWNED = {"okbase_order_emails": ["me@eli-beams.eu"], "okbase_order_codes": BLOB}
GOOD = WORDS[0]

# The code is the protection, and by default the only one: lunch is ordered
# from more than one account, and a list of addresses to keep in step with that
# is a lock that mostly locks its owner out.
check("any sender with a valid code may order",
      om.may_order({"okbase_order_codes": BLOB}, "anybody@anywhere.eu",
                   GOOD)[0], True)
check("even with no address at all",
      om.may_order({"okbase_order_codes": BLOB}, "", GOOD)[0], True)
check("but not without a code",
      om.may_order({"okbase_order_codes": BLOB}, "anybody@anywhere.eu", "")[0],
      False)
check("and not with a wrong one",
      om.may_order({"okbase_order_codes": BLOB}, "anybody@anywhere.eu",
                   "notonthelist")[0], False)

print("")
print("restricting to certain senders is optional")

check("with a list, a listed sender may",
      om.may_order(OWNED, "ME@eli-beams.eu", GOOD)[0], True)
check("and an unlisted one may not",
      om.may_order(OWNED, "someone@eli-beams.eu", GOOD)[0], False)
check("the refusal names what arrived",
      "someone@eli-beams.eu" in
      om.may_order(OWNED, "someone@eli-beams.eu", GOOD)[1], True)
check("no address at all is said in words",
      "no address at all" in om.may_order(OWNED, "", GOOD)[1], True)
check("several addresses all work",
      [om.may_order({"okbase_order_emails": ["a@x.eu", "b@y.eu"],
                     "okbase_order_codes": BLOB}, who, GOOD)[0]
       for who in ("a@x.eu", "B@Y.EU")], [True, True])
check("a single string works like a list",
      om.may_order({"okbase_order_emails": "me@eli-beams.eu",
                    "okbase_order_codes": BLOB},
                   "me@eli-beams.eu", GOOD)[0], True)
check("a list of blanks is no list at all",
      om.may_order({"okbase_order_emails": ["", "  "],
                    "okbase_order_codes": BLOB}, "anyone@x.y", GOOD)[0], True)

print("")
print("the one-time codes")

words, blob = om.new_order_codes(20)
check("a list is made", len(words), 20)
check("as many fingerprints as codes", len(blob["hashes"]), 20)
check("no two the same", len(set(words)), 20)
check("they read like words",
      all(w.isalpha() and w.islower() for w in words), True)
check("and are the length promised",
      {len(w) for w in words}, {om.ORDER_CODE_SYLLABLES * 2})
check("the stored blob does not contain the codes",
      any(w in h for w in words for h in blob["hashes"]), False)
check("nor does the salt", any(w in blob["salt"] for w in words), False)
check("every code is found on its own list",
      all(om.code_on_list({"okbase_order_codes": blob}, w) for w in words),
      True)
check("a code from another list is not",
      om.code_on_list({"okbase_order_codes": blob}, GOOD), False)
check("a hundred is the default", len(om.new_order_codes()[0]), 100)

check("a code read off a phone is forgiven its punctuation",
      om.normalise_code(" Bakoli. "), "bakoli")
check("but not turned into something else",
      om.normalise_code("ba-ko-li"), "bakoli")
check("carelessly typed, still found",
      om.code_on_list({"okbase_order_codes": blob},
                      " " + words[3].upper() + "."), True)

check("counting what is left", om.codes_left(OWNED), 4)
check("nothing stored counts as nothing", om.codes_left({}), 0)
check("a damaged blob counts as nothing",
      om.codes_left({"okbase_order_codes": {"hashes": ["x"]}}), 0)
check("and never crashes",
      om.code_on_list({"okbase_order_codes": {"salt": "!!", "hashes": ["x"]}},
                      "bakoli"), False)

check("no codes at all means no ordering",
      om.may_order({"okbase_order_emails": ["me@eli-beams.eu"]},
                   "me@eli-beams.eu", GOOD)[0], False)
check("and says where to make some",
      "Ordering codes" in
      om.may_order({"okbase_order_emails": ["me@eli-beams.eu"]},
                   "me@eli-beams.eu", GOOD)[1], True)
check("the owner without a code is refused",
      om.may_order(OWNED, "me@eli-beams.eu", "")[0], False)
check("and is told how to give one",
      "pin:" in om.may_order(OWNED, "me@eli-beams.eu", "")[1], True)
check("a code not on the list is turned away here, before the portal",
      om.may_order(OWNED, "me@eli-beams.eu", "notonthelist")[0], False)
check("and it is never echoed back",
      "notonthelist" in om.may_order(OWNED, "me@eli-beams.eu",
                                     "notonthelist")[1], False)
check("but a good one is not struck off by the check",
      (om.may_order(OWNED, "me@eli-beams.eu", GOOD)[0],
       om.codes_left(OWNED)), (True, 4))

# Spending. The real spend writes %APPDATA%, so the disk read/write is
# redirected: what is under test is the striking-off, not the file.
saved: dict = {}
_real_load, _real_save = om.load_user_settings, om.save_user_settings
om.load_user_settings = lambda: dict(saved)
om.save_user_settings = lambda values: (saved.update(values), "")[1]
try:
    words, blob = om.new_order_codes(3)
    live = {"okbase_order_codes": blob}
    saved.clear()
    saved.update({"okbase_order_codes": blob})

    ok, why = om.spend_order_code(live, words[1])
    check("a code on the list is accepted", ok, True)
    check("no complaint", why, "")
    check("it is struck off the file",
          len(saved["okbase_order_codes"]["hashes"]), 2)
    check("and off the caller's copy too", om.codes_left(live), 2)
    check("the one spent is the one gone",
          om.code_on_list(saved, words[1]), False)
    check("the others still work", om.code_on_list(saved, words[0]), True)

    ok, why = om.spend_order_code(live, words[1])
    check("the same code does not work twice", ok, False)
    check("and says so", "used already" in why, True)
    check("nothing else was spent", om.codes_left(live), 2)

    ok, why = om.spend_order_code(live, "notonthelist")
    check("a made-up code is refused", ok, False)
    check("and never echoed", "notonthelist" in why, False)
    check("and costs nothing", om.codes_left(live), 2)

    ok, why = om.spend_order_code(live, "  " + words[0].upper() + ".")
    check("a code typed carelessly still works", ok, True)

    om.save_user_settings = lambda values: "the share is not answering"
    before = om.codes_left(live)
    ok, why = om.spend_order_code(live, words[2])
    check("a code that could not be struck off is refused", ok, False)
    check("rather than let it work twice", om.codes_left(live), before)

    om.save_user_settings = lambda values: (saved.update(values), "")[1]
    saved.clear()
    ok, why = om.spend_order_code({}, "bakoli")
    check("an empty list refuses", ok, False)
    check("and says where to make one", "Ordering codes" in why, True)
    ok, why = om.spend_order_code(live, "")
    check("no code given, nothing spent", ok, False)
finally:
    om.load_user_settings, om.save_user_settings = _real_load, _real_save

# The code rides on the command, and must survive the parser untouched.
r = req("order friday 1 pin:bakoli")
check("the code comes off the command", r.password, "bakoli")
check("and does not disturb the order", r.picks, (("HLAVNI_JIDLO", 1),))
check("nor the day", r.day, date(2026, 9, 11))
check("capital letters survive", req("order friday 1 pw:AbC").password, "AbC")
check("the label may be Czech", req("order friday 1 kod:AbC").password, "AbC")
check("it may sit anywhere", req("order pin:AbC friday 1").picks,
      (("HLAVNI_JIDLO", 1),))
check("a code that reads like a day is not read as one",
      req("order friday 1 pin:monday").day, date(2026, 9, 11))
check("a code that reads like a number is not a pick",
      req("order friday 1 pin:2").picks, (("HLAVNI_JIDLO", 1),))
check("cancel carries one too", req("cancel friday pin:AbC").password, "AbC")
check("cancel with a code is still a cancel",
      req("cancel friday pin:AbC").mode, "cancel")
check("no code given, none invented", req("order friday 1").password, "")
check("reading commands are unaffected", req("friday").password, "")


# --------------------------------------------------------------------------- #
print("")
print("the whole round trip, against a made-up portal")


class FakePortal:
    """Answers `nacti-vse` and remembers what `uloz` was sent.

    Keeps its own copy of the orders and applies a save to it, so reading back
    after a save behaves the way the real portal does — which is the only way to
    test that change_order verifies its own work.
    """

    def __init__(self, orders: dict, accept: bool = True, apply: bool = True):
        self.orders = {k: [dict(e) for e in v] for k, v in orders.items()}
        self.accept = accept
        self.apply = apply
        self.saved: list[dict] = []
        self.reads = 0

    class _Reply:
        def __init__(self, code: int, payload=None, text: str = ""):
            self.status_code = code
            self._payload = payload
            self.text = text
            self.content = b"x" * 10

        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    def post(self, url, json=None, timeout=None):
        if url.endswith("nacti-vse"):
            self.reads += 1
            return self._Reply(200, {"listky": PAYLOAD["listky"],
                                     "objednavky": self.orders})
        if url.endswith("uloz"):
            self.saved.append(json)
            if not self.accept:
                return self._Reply(500, None, "Chyba pri ukladani")
            if self.apply:
                for day, entries in (json.get("objednavky") or {}).items():
                    entry = entries[0]
                    ids = list((entry.get("polozkyIdMap") or {}).values())
                    self.orders[day] = [] if not ids else [
                        {"id": 900001, "datumVydeje": day, "stav": "OBJEDNANO",
                         "zadna": False,
                         "polozky": [{"id": i, "jidlo": None} for i in ids]}]
            return self._Reply(200, {"ok": True})
        raise AssertionError(f"unexpected url {url}")


SETTINGS = {"okbase_user_id": 6507822, "okbase_canteen_id": 1,
            "okbase_timeout_s": 5}


def run(day: date, picks=(), clear=False, **kw):
    portal = FakePortal(PAYLOAD["objednavky"], **kw)
    om.open_session = lambda s: (portal, om.BASE_DEFAULT, 5.0, "")
    om.merged_cookie_header = lambda s, prev="": "JSESSIONID=fake"
    om.secrets_resolved_cookie = lambda s: ""
    return portal, om.change_order(SETTINGS, day, picks, clear)


_real_open, _real_merge, _real_cookie = (
    om.open_session, om.merged_cookie_header, om.secrets_resolved_cookie)

portal, out = run(date(2026, 9, 11), (("HLAVNI_JIDLO", 1),))
check("no complaint", out.error, "")
check("it ordered the meat loaf", [m.item_id for m in out.ordered], [572642])
check("one save was sent", len(portal.saved), 1)
check("it was read before and after", portal.reads, 2)
body = portal.saved[0]
check("Friday's entry is the new-order shape", body["objednavky"]["2026-09-11"],
      [{"polozkyIdMap": {"HLAVNI_JIDLO": 572642}, "objednavkaId": 0}])
check("Thursday's order was NOT wiped", body["objednavky"]["2026-09-10"],
      [{"polozkyIdMap": {"POLEVKA": 572633, "HLAVNI_JIDLO": 572636},
        "zadna": False}])
check("the save carries the dates of the week being changed",
      body["weekStart"][:10], "2026-09-07")
check("and the ids the portal wants", (body["userId"], body["jidelnaId"]),
      (6507822, 1))

portal, out = run(date(2026, 9, 10), clear=True)
check("cancelling says what was there", [m.item_id for m in out.was],
      [572633, 572636])
check("cancelling leaves nothing", out.ordered, ())
check("cancelling sends 'none'", portal.saved[0]["objednavky"]["2026-09-10"],
      [{"zadna": True}])

portal, out = run(date(2026, 9, 9), (("HLAVNI_JIDLO", 1),))
check("a closed day is refused before anything is sent",
      "closed" in out.error, True)
check("and nothing was sent", portal.saved, [])

portal, out = run(date(2026, 9, 12), (("HLAVNI_JIDLO", 1),))
check("a day with no menu is refused", "no menu" in out.error, True)

portal, out = run(date(2026, 9, 11), (("HLAVNI_JIDLO", 9),))
check("a number past the end never reaches the portal", portal.saved, [])
check("and says so", "does not exist" in out.error, True)

portal, out = run(date(2026, 9, 11), (("HLAVNI_JIDLO", 1),), accept=False)
check("a refusal is quoted", "500" in out.error, True)
check("and the portal's own words are kept",
      "Chyba" in out.error, True)

portal, out = run(date(2026, 9, 11), (("HLAVNI_JIDLO", 1),), apply=False)
check("a save that is accepted but not kept is caught",
      "did not keep it" in out.error, True)


# --------------------------------------------------------------------------- #
print("")
print("when the one-time code is spent")

# The whole point of the `authorise` hook: a code must be struck off if and
# only if the portal is actually being asked to change something. A day that
# turns out to be closed, or a meal number that does not exist, must cost
# nothing — otherwise a person who mistypes runs out of codes.

def run_authorised(day: date, picks=(), clear=False, **kw):
    spent: list[str] = []
    portal = FakePortal(PAYLOAD["objednavky"], **kw)
    om.open_session = lambda s: (portal, om.BASE_DEFAULT, 5.0, "")
    om.merged_cookie_header = lambda s, prev="": "JSESSIONID=fake"
    om.secrets_resolved_cookie = lambda s: ""

    def authorise():
        spent.append("code")
        return True, ""

    out = om.change_order(SETTINGS, day, picks, clear, authorise=authorise)
    return portal, out, spent


portal, out, spent = run_authorised(date(2026, 9, 11), (("HLAVNI_JIDLO", 1),))
check("a real change spends the code", spent, ["code"])
check("and it went through", out.error, "")
check("spent once, not twice", len(spent), 1)

portal, out, spent = run_authorised(date(2026, 9, 9), (("HLAVNI_JIDLO", 1),))
check("a closed day costs no code", spent, [])
check("and nothing was sent", portal.saved, [])

portal, out, spent = run_authorised(date(2026, 9, 11), (("HLAVNI_JIDLO", 9),))
check("a meal that does not exist costs no code", spent, [])

portal, out, spent = run_authorised(date(2026, 9, 12), (("HLAVNI_JIDLO", 1),))
check("a day with no menu costs no code", spent, [])

# Thursday is already booked in this payload: soup 1 (572633) and main 2
# (572636). That is the case the merging is for.
THU = date(2026, 9, 10)

portal, out, spent = run_authorised(THU, (("HLAVNI_JIDLO", 1),))
check("a booked day is re-ordered without any extra word", out.already, False)
check("and spends the code", spent, ["code"])
check("and sends the swap", len(portal.saved), 1)
check("as an existing order, not a new one",
      "zadna" in portal.saved[0]["objednavky"]["2026-09-10"][0], True)
check("the soup nobody mentioned is kept",
      portal.saved[0]["objednavky"]["2026-09-10"][0]["polozkyIdMap"],
      {"POLEVKA": 572633, "HLAVNI_JIDLO": 572635})

# The case as it was asked for: soup 1 + main 2 booked, "soup 2 main 2" sent.
portal, out, spent = run_authorised(
    THU, (("HLAVNI_JIDLO", 2), ("POLEVKA", 2)))
check("soup 2 main 2 over soup 1 main 2 goes through", out.error, "")
check("and only the soup moved",
      portal.saved[0]["objednavky"]["2026-09-10"][0]["polozkyIdMap"],
      {"POLEVKA": 572634, "HLAVNI_JIDLO": 572636})

portal, out, spent = run_authorised(THU, (("POLEVKA", 0),))
check("0 takes that course off",
      portal.saved[0]["objednavky"]["2026-09-10"][0]["polozkyIdMap"],
      {"HLAVNI_JIDLO": 572636})
check("and leaves the day ordered", [m.item_id for m in out.ordered], [572636])

portal, out, spent = run_authorised(THU, (("POLEVKA", 0), ("HLAVNI_JIDLO", 0)))
check("every course off empties the day",
      portal.saved[0]["objednavky"]["2026-09-10"], [{"zadna": True}])
check("and nothing is left ordered", out.ordered, ())

# Asking for exactly what is there is the one thing still turned away, and it
# is turned away before the code is spent.
portal, out, spent = run_authorised(
    THU, (("POLEVKA", 1), ("HLAVNI_JIDLO", 2)))
check("no-change costs no code", spent, [])
check("and nothing was sent", portal.saved, [])
check("it is not an error", out.error, "")
check("it says it is already there", out.already, True)
check("and names what is on it", [m.item_id for m in out.was],
      [572633, 572636])

# Cancelling a booked day is the whole point of cancel — never turned away.
portal, out, spent = run_authorised(date(2026, 9, 10), clear=True)
check("cancelling a booked day is never turned away", out.already, False)
check("and does spend a code", spent, ["code"])

# And the other way round: a code the hook refuses must stop the save dead.
portal = FakePortal(PAYLOAD["objednavky"])
om.open_session = lambda s: (portal, om.BASE_DEFAULT, 5.0, "")
out = om.change_order(SETTINGS, date(2026, 9, 11), (("HLAVNI_JIDLO", 1),),
                      authorise=lambda: (False, "that code has been used"))
check("a refused code stops the save", portal.saved, [])
check("and says why", "been used" in out.error, True)

om.open_session, om.merged_cookie_header, om.secrets_resolved_cookie = (
    _real_open, _real_merge, _real_cookie)


# --------------------------------------------------------------------------- #
print("")
print("the help text")

# One block, used by both /help and /food help, so the two cannot drift apart —
# and last in each, because ordering is the only thing here that writes to
# somebody's HR portal.
check("ordering has a section of its own",
      om.ORDER_HELP.startswith("**🍽 Ordering lunch**"), True)
check("it shows the whole command",
      "/food order friday 1 pin:bakoli" in om.ORDER_HELP, True)
for piece in ("order", "the day", "the number", "`pin:`"):
    check(f"it explains {piece}", f"**{piece}**" in om.ORDER_HELP, True)
check("cancelling is there too",
      "/food cancel friday" in om.ORDER_HELP, True)
check("and the listing", "/food orders" in om.ORDER_HELP, True)
check("it says a bare number is the main course",
      "main course" in om.ORDER_HELP, True)
check("and where the codes come from",
      "Settings → Canteen menu → Ordering codes" in om.ORDER_HELP, True)

check("/food help carries it", om.ORDER_HELP in om.FOOD_HELP, True)
check("and puts it last", om.FOOD_HELP.rstrip().endswith(
    om.ORDER_HELP.rstrip()), True)
check("the menu's own words still come first",
      om.FOOD_HELP.index("`/food week`") < om.FOOD_HELP.index(om.ORDER_HELP),
      True)


print("")
if FAILED:
    print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
    raise SystemExit(1)
print("all ok")
