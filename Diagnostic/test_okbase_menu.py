"""Unit tests for the canteen menu reader (no Qt, no network, no real clock).

Run with:  python test_okbase_menu.py

The OKbase payload shape is not documented, so the reader is written as a
tolerant walk (okbase_menu.parse_menu). These tests pin that tolerance down:
both payload shapes this family of APIs uses, the date and price spellings, and
above all that a day the cache cannot answer for says so instead of showing
another day's food.
"""

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

import bot_commands  # noqa: E402
import okbase_menu as om  # noqa: E402

NOW = datetime(2026, 8, 26, 9, 15)          # Wednesday morning
TODAY = NOW.date()
MONDAY = date(2026, 8, 24)

# Nested shape: a list of days, each with its meals inside.
NESTED = [
    {"datum": "2026-08-24", "jidla": [
        {"nazev": "Česnečka", "cena": 25, "jidloTyp": {"nazev": "soup"}},
        {"nazev": "Vepřo knedlo zelo", "cena": 95.0,
         "jidloTyp": {"nazev": "main course"}},
    ]},
    {"datum": "2026-08-26", "jidla": [
        {"nazev": "Kulajda", "cena": 28, "jidloTyp": {"nazev": "soup"}},
        {"nazev": "Svíčková na smetaně", "cena": 105,
         "jidloTyp": {"nazev": "main course"}},
        {"nazev": "Zapečené brambory", "cena": 89,
         "jidloTyp": {"nazev": "main course"}},
    ]},
    {"datum": "2026-08-29", "jidla": []},
]

# Flat shape: every meal carries its own date, Czech date format, price a string.
FLAT = {"content": [
    {"datumVydeje": "27.08.2026", "nazevJidla": "Gulášová polévka",
     "cenaCelkem": "30", "typJidla": "soup"},
    {"datumVydeje": "27.08.2026", "nazevJidla": "Smažený řízek",
     "cenaCelkem": "110,50", "typJidla": "main course"},
]}


# The real answer, cut down from a live capture on 2026-08-26. This is the shape
# that matters — the two above only prove the reader is not tied to it.
REAL = {
    "listky": {
        "2026-08-26": {
            "id": 565293, "stav": "UZAVRENY", "jidelnaId": 1,
            "datumVydeje": "2026-08-26", "orgId": None, "svatek": None,
            "polozky": [
                {"id": 565294, "poradi": 1, "hodnoceni": None, "jidlo": {
                    "cJidloId": 136055,
                    "nazev": "Hovězí vývar / Beef broth",
                    "kod": None, "popis": "9", "typ": "POLEVKA",
                    "kategorie": [{"kod": 2, "textCs": "bezlepkové",
                                   "organizace": {"orgId": 1,
                                                  "nazev": "ELI ERIC"}}],
                    "dodavatel": "A Supplier",
                    "aktualniCenaPlna": 30, "aktualniCenaDotovana": 30}},
                {"id": 565295, "poradi": 2, "jidlo": {
                    "cJidloId": 136056,
                    "nazev": "Svíčková na smetaně / Beef sirloin",
                    "popis": "1,3,7", "typ": "HLAVNI_JIDLO",
                    "kategorie": [], "dodavatel": "A Supplier",
                    "aktualniCenaPlna": 120, "aktualniCenaDotovana": 45}},
            ],
        },
    },
    # The person's own orders. Same nesting, but every name is null, so the
    # reader must find nothing here rather than duplicating the day.
    "objednavky": {
        "2026-08-26": [{
            "id": 565655, "datumVydeje": "2026-08-26", "stav": "ODEBRANO",
            "uzivatel": {"id": 6507822, "celeJmeno": None},
            "jidelna": {"id": 1, "jmeno": "Výchozí"},
            "polozky": [{"id": 565294, "jidlo": {
                "cJidloId": 136055, "nazev": None, "typ": None,
                "aktualniCenaDotovana": None}}],
        }],
    },
}


def cache_of(payload, fetched=NOW):
    return om.build_cache(om.parse_menu(payload), fetched)


# --------------------------------------------------------------------------- #
# reading the payload
# --------------------------------------------------------------------------- #

def test_the_real_answer_is_read():
    """Pinned against a live capture: listky[date].polozky[].jidlo."""
    days = om.parse_menu(REAL)
    assert list(days) == ["2026-08-26"], list(days)
    meals = days["2026-08-26"]
    assert [m.name for m in meals] == ["Hovězí vývar / Beef broth",
                                       "Svíčková na smetaně / Beef sirloin"]
    # The SUBSIDISED price is the one a person pays — 45, not the full 120.
    assert [m.price for m in meals] == ["30 Kč", "45 Kč"]
    # The course arrives as a code and must not be shown as one.
    assert [m.kind for m in meals] == ["soup", "main course"]


def test_organisation_name_is_not_taken_for_a_meal():
    """`kategorie[].organizace.nazev` is "ELI ERIC" — one careless recursion and
    the canteen serves the institute for lunch."""
    names = [m.name for m in om.parse_menu(REAL)["2026-08-26"]]
    assert not any("ELI" in n for n in names), names
    assert len(names) == 2


def test_allergen_numbers_are_not_a_meal_name():
    """`popis` holds allergen numbers ("9"), so it must not stand in for a name."""
    payload = {"listky": {"2026-08-26": {"polozky": [
        {"jidlo": {"popis": "9", "typ": "POLEVKA",
                   "aktualniCenaDotovana": 30}}]}}}
    assert om.parse_menu(payload) == {}


def test_own_orders_do_not_duplicate_the_day():
    meals = om.parse_menu(REAL)["2026-08-26"]
    assert len(meals) == 2, [m.name for m in meals]


def test_unknown_course_codes_still_read_as_words():
    assert om._pretty_kind("POLEVKA") == "soup"
    assert om._pretty_kind("HLAVNI_JIDLO") == "main course"
    assert om._pretty_kind("NECO_NOVEHO") == "neco noveho"
    assert om._pretty_kind("soup") == "soup"
    assert om._pretty_kind(None) == ""


def test_nested_payload_is_read():
    days = om.parse_menu(NESTED)
    assert sorted(days) == ["2026-08-24", "2026-08-26"], sorted(days)
    assert [m.name for m in days["2026-08-26"]] == [
        "Kulajda", "Svíčková na smetaně", "Zapečené brambory"]


def test_flat_payload_is_read():
    days = om.parse_menu(FLAT)
    assert list(days) == ["2026-08-27"], list(days)
    meals = days["2026-08-27"]
    assert meals[0].name == "Gulášová polévka"
    assert meals[0].price == "30 Kč", meals[0].price
    assert meals[1].price == "110.5 Kč", meals[1].price
    assert meals[1].kind == "main course"


def test_prices_and_kinds():
    days = om.parse_menu(NESTED)
    assert days["2026-08-24"][0].price == "25 Kč"
    assert days["2026-08-24"][1].price == "95 Kč"        # 95.0 must not read "95.0"
    assert days["2026-08-24"][0].kind == "soup"


def test_epoch_dates_are_understood():
    payload = [{"datum": 1787702400000, "nazev": "Oběd", "cena": 1}]
    days = om.parse_menu(payload)
    assert len(days) == 1 and list(days)[0].startswith("2026-08-"), days


def test_duplicates_are_dropped():
    payload = [{"datum": "2026-08-26", "jidla": [
        {"nazev": "Kulajda", "cena": 28},
        {"nazev": "kulajda", "cena": 28},
    ]}]
    assert len(om.parse_menu(payload)["2026-08-26"]) == 1


def test_garbage_payload_yields_nothing():
    for payload in (None, [], {}, {"error": "nope"}, "text", 17):
        assert om.parse_menu(payload) == {}, payload


def test_canteen_name_is_not_taken_for_a_meal():
    payload = [{"datum": "2026-08-26",
                "jidelna": {"nazev": "Canteen ELI"},
                "jidla": [{"nazev": "Kulajda", "cena": 28}]}]
    names = [m.name for m in om.parse_menu(payload)["2026-08-26"]]
    assert names == ["Kulajda"], names


# --------------------------------------------------------------------------- #
# rendering one day
# --------------------------------------------------------------------------- #

def test_today_lists_meals_and_prices():
    out = om.render_day(cache_of(NESTED), TODAY, NOW)
    assert "Wednesday 26.08." in out
    assert "1) **Kulajda** — 28 Kč" in out, out
    assert "Svíčková" in out
    assert "⚠" not in out                      # fresh cache: no warning


def test_courses_get_headings_and_their_own_numbering():
    """The course used to be a label at the END of each line, right where the
    eye looks for the price. It is a heading now, and each group counts from 1."""
    out = om.render_day(cache_of(NESTED), TODAY, NOW)
    assert "**🥣 Soups**" in out, out
    assert "**🍛 Main courses**" in out, out
    soups = out.index("🥣 Soups")
    mains = out.index("🍛 Main courses")
    assert soups < mains, "soups come first"
    assert out.count("1) ") == 2, out          # one numbering per group
    assert "_soup_" not in out                 # never as a trailing label


def test_unknown_course_still_gets_a_group():
    payload = [{"datum": "2026-08-26", "jidla": [
        {"nazev": "Něco nového", "cena": 10, "typ": "SVACINA"}]}]
    out = om.render_day(cache_of(payload), TODAY, NOW)
    assert "Svacina" in out or "svacina" in out, out
    assert "Něco nového" in out


def test_day_inside_the_fetched_span_with_no_meals():
    out = om.render_day(cache_of(NESTED), date(2026, 8, 25), NOW)
    assert "no meals offered" in out, out


def test_day_outside_the_fetched_span_says_so():
    out = om.render_day(cache_of(NESTED), date(2026, 9, 30), NOW)
    assert "no menu for that day" in out, out
    assert "Kulajda" not in out                # never show another day's food


def test_empty_cache_is_polite():
    out = om.render_day({}, TODAY, NOW)
    assert "no menu saved yet" in out
    assert "refresh" in out


def test_corrupt_cache_file_reads_as_empty(tmp_name="okbase_test_corrupt.json"):
    from pathlib import Path
    path = Path(os.path.dirname(__file__)) / tmp_name
    try:
        path.write_text('{"days": {"2026-08-26": [{"nam', encoding="utf-8")
        assert om.read_cache_file(path) == {}
        path.write_text("", encoding="utf-8")
        assert om.read_cache_file(path) == {}
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def test_stale_cache_announces_itself():
    old = cache_of(NESTED, datetime(2026, 8, 22, 6, 0))     # 4 days before NOW
    out = om.render_day(old, TODAY, NOW)
    assert out.startswith("**⚠"), out
    assert "days ago" in out or "h ago" in out


def test_fresh_cache_shows_when_it_was_read():
    out = om.render_day(cache_of(NESTED), TODAY, NOW)
    assert "Read from OKbase 2026-08-26 09:15" in out, out


# --------------------------------------------------------------------------- #
# rendering a week
# --------------------------------------------------------------------------- #

def test_week_shows_only_days_with_meals():
    out = om.render_week(cache_of(NESTED), MONDAY, NOW)
    assert "Monday 24.08." in out
    assert "Wednesday 26.08." in out
    assert "Tuesday" not in out                # nothing offered, so not listed
    assert "Saturday" not in out
    assert "🥣 Soups" in out                    # grouped there too


def test_week_with_nothing_known_says_so():
    out = om.render_week(cache_of(NESTED), date(2026, 12, 7), NOW)
    assert "no menu for that week" in out, out


# --------------------------------------------------------------------------- #
# the command words
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# two languages in one field
# --------------------------------------------------------------------------- #

BILINGUAL = [{"datum": "2026-08-26", "jidla": [
    {"nazev": "Kuřecí vývar/Chicken broth", "cena": 30, "typ": "POLEVKA"},
    {"nazev": "Cuketový krém", "cena": 30, "typ": "POLEVKA"},
    {"nazev": "Svíčková na smetaně / Beef sirloin", "cena": 147,
     "typ": "HLAVNI_JIDLO"},
    {"nazev": "Letní salátová miska :)", "cena": 147, "typ": "HLAVNI_JIDLO"},
]}]


def test_splitting_the_two_languages():
    assert om.split_languages("Kuřecí vývar/Chicken broth") == \
        ("Kuřecí vývar", "Chicken broth")
    assert om.split_languages("Svíčková na smetaně / Beef sirloin") == \
        ("Svíčková na smetaně", "Beef sirloin")
    # English first happens too; position must not decide, diacritics must.
    assert om.split_languages("Beef broth / Hovězí vývar") == \
        ("Hovězí vývar", "Beef broth")
    # Czech only.
    assert om.split_languages("Letní salátová miska :)") == \
        ("Letní salátová miska :)", "")


def test_a_slash_that_is_not_a_language_break_is_left_alone():
    """"vepřové/kuřecí" is one dish in one language, not two names."""
    assert om.split_languages("Řízek vepřový/kuřecí") == \
        ("Řízek vepřový/kuřecí", "")
    # Three parts: no honest way to tell, so keep it whole.
    assert om.split_languages("a / b / c") == ("a / b / c", "")


def test_both_languages_are_shown_on_separate_lines():
    out = om.render_day(cache_of(BILINGUAL), TODAY, NOW)
    assert "1) **Kuřecí vývar** — 30 Kč" in out, out
    assert "_Chicken broth_" in out, out
    # The slash form is what made it unreadable — it must be gone.
    assert "vývar/Chicken" not in out
    # A Czech-only meal must not sprout an empty second line.
    assert "Cuketový krém** — 30 Kč\n    __" not in out


def test_czech_only_and_english_only():
    cs = om.render_day(cache_of(BILINGUAL), TODAY, NOW, lang="cs")
    assert "Kuřecí vývar" in cs and "Chicken broth" not in cs, cs
    en = om.render_day(cache_of(BILINGUAL), TODAY, NOW, lang="en")
    assert "Chicken broth" in en and "Kuřecí vývar" not in en, en
    # No English name for this one, so it stays Czech rather than vanishing.
    assert "Letní salátová miska" in en, en


def test_language_words_in_the_command():
    for text, want in (("; cz", "cs"), ("cz", "cs"), ("; en", "en"),
                       ("english", "en"), ("", "both"), ("week; en", "en")):
        assert om.parse_food_args(text, TODAY).lang == want, text
    # The language must not eat the day.
    req = om.parse_food_args("week; en", TODAY)
    assert req.mode == "week" and req.monday == MONDAY
    req = om.parse_food_args("friday; cz", TODAY)
    assert req.mode == "day" and req.day == date(2026, 8, 28)


def test_the_separator_may_be_glued_to_the_command():
    """"/food; cz" used to arrive as the command "/food;" and match nothing."""
    for text in ("/food; cz", "/food;cz", "/food ; cz"):
        pc = bot_commands.parse_command(text)
        assert pc.cmd == "/food", (text, pc.cmd)
        assert om.parse_food_args(pc.args, TODAY).lang == "cs", text


def test_plain_food_is_today():
    req = om.parse_food_args("", TODAY)
    assert req.mode == "day" and req.day == TODAY


def test_week_words():
    assert om.parse_food_args("week", TODAY).monday == MONDAY
    assert om.parse_food_args("next week", TODAY).monday == date(2026, 8, 31)
    assert om.parse_food_args("this week", TODAY).monday == MONDAY
    assert om.parse_food_args("last week", TODAY).monday == date(2026, 8, 17)


def test_day_words():
    assert om.parse_food_args("tomorrow", TODAY).day == date(2026, 8, 27)
    assert om.parse_food_args("today", TODAY).day == TODAY
    assert om.parse_food_args("friday", TODAY).day == date(2026, 8, 28)
    assert om.parse_food_args("next monday", TODAY).day == date(2026, 8, 31)


def test_dates():
    assert om.parse_food_args("27.8.", TODAY).day == date(2026, 8, 27)
    assert om.parse_food_args("27.8.2026", TODAY).day == date(2026, 8, 27)
    assert om.parse_food_args("2026-08-27", TODAY).day == date(2026, 8, 27)


def test_refresh_and_help():
    assert om.parse_food_args("refresh", TODAY).mode == "refresh"
    assert om.parse_food_args("help", TODAY).mode == "help"
    assert om.parse_food_args("status", TODAY).mode == "status"
    assert om.parse_food_args("info", TODAY).mode == "status"


def test_status_says_what_is_saved_and_how_old():
    out = om.answer_food("status", cache_of(NESTED), NOW)
    assert "2 " in out and "2026-08-24 to 2026-08-26" in out, out
    assert "5 meal(s)" in out, out
    assert "0 h ago" in out, out
    assert "⚠" not in out                      # fresh, so no warning

    old = om.answer_food("status", cache_of(NESTED, datetime(2026, 8, 22, 6)), NOW)
    assert "⚠" in old and "out of date" in old, old

    empty = om.answer_food("status", {}, NOW)
    assert "none saved yet" in empty, empty


def test_nonsense_gets_an_explanation_not_a_crash():
    req = om.parse_food_args("banana", TODAY)
    assert req.error and "banana" in req.error
    out = om.answer_food("banana", cache_of(NESTED), NOW)
    assert out.startswith("⚠") and "/food week" in out


def test_answer_food_routes_day_and_week():
    cache = cache_of(NESTED)
    assert "Wednesday 26.08." in om.answer_food("", cache, NOW)
    assert "Menu 24.08. – 30.08.2026" in om.answer_food("week", cache, NOW)


# --------------------------------------------------------------------------- #
# after lunch, a bare /food means tomorrow
# --------------------------------------------------------------------------- #

def _week_cache(day_numbers, fetched=NOW):
    """A cache holding one meal on each named August day."""
    days = {date(2026, 8, n).isoformat(): [om.Meal("Guláš / Goulash", "95 Kč",
                                                   "main course")]
            for n in day_numbers}
    return om.build_cache(days, fetched)


def test_a_bare_food_moves_to_tomorrow_after_half_past_two():
    cache = _week_cache([26, 27])                       # Wed and Thu
    before = datetime(2026, 8, 26, 14, 29)
    after = datetime(2026, 8, 26, 14, 30)
    assert om.next_food_day(cache, before) == date(2026, 8, 26)
    assert om.next_food_day(cache, after) == date(2026, 8, 27)
    assert "Wednesday 26.08." in om.answer_food("", cache, before)
    assert "Thursday 27.08." in om.answer_food("", cache, after)


def test_only_a_bare_food_moves():
    """A day that was asked for by name is the day that is shown."""
    cache = _week_cache([26, 27])
    after = datetime(2026, 8, 26, 16, 0)
    assert "Wednesday 26.08." in om.answer_food("today", cache, after)
    assert "Wednesday 26.08." in om.answer_food("26.8.", cache, after)
    assert "Menu 24.08. – 30.08.2026" in om.answer_food("week", cache, after)


def test_friday_afternoon_lands_on_monday_not_on_an_empty_saturday():
    """The canteen is shut at the weekend, so 'tomorrow' would be no answer."""
    cache = _week_cache([28, 31])                       # Friday, then Monday
    friday_pm = datetime(2026, 8, 28, 15, 0)
    assert om.next_food_day(cache, friday_pm) == date(2026, 8, 31)
    assert "Monday 31.08." in om.answer_food("", cache, friday_pm)


def test_with_nothing_ahead_it_still_names_tomorrow():
    """No later day saved: say tomorrow and let render_day admit it has none —
    silently falling back to today would pass lunch that is over off as lunch."""
    cache = _week_cache([26])
    after = datetime(2026, 8, 26, 15, 0)
    assert om.next_food_day(cache, after) == date(2026, 8, 27)
    assert "Thursday 27.08." in om.answer_food("", cache, after)


# --------------------------------------------------------------------------- #
# the request bodies tried against the portal
# --------------------------------------------------------------------------- #

def test_filter_candidates_lead_with_the_real_shape():
    bodies = om.filter_candidates(date(2026, 7, 27), date(2026, 9, 6),
                                  canteen_id=4, user_id=6507822)
    assert bodies, "no candidate request bodies"
    for body in bodies:
        json.dumps(body)                        # must be postable as-is
    first = bodies[0]
    # Captured from the portal's own front end; the shape is not a guess.
    assert first["datumOd"].startswith("2026-07-27T00:00:00.000+")
    assert first["datumDo"].startswith("2026-09-06T")
    assert first["weekStart"].startswith("2026-07-27T")   # that week's Monday
    assert first["weekEnd"].startswith("2026-08-02T")
    assert first["jidelnaId"] == 4 and first["userId"] == 6507822
    assert first["objednavky"] == {}
    # A userId-free variant, in case the portal stops wanting it.
    assert any("userId" not in b and "weekStart" in b for b in bodies)
    assert om.filter_candidates(MONDAY, MONDAY)[0]["jidelnaId"] == 1


def test_stored_filter_is_retargeted_not_replayed():
    """The whole point: a captured body must not keep asking for its own week."""
    captured = {
        "datumOd": "2026-07-27T00:00:00.000+02:00",
        "datumDo": "2026-09-06T00:00:00.000+02:00",
        "weekStart": "2026-08-24T00:00:00.000+02:00",
        "weekEnd": "2026-08-30T00:00:00.000+02:00",
        "userId": 6507822, "jidelnaId": 1, "objednavky": {},
    }
    out = om.retarget_filter(captured, date(2026, 11, 2), date(2026, 11, 15))
    assert out["datumOd"].startswith("2026-11-02T")
    assert out["datumDo"].startswith("2026-11-15T")
    assert out["weekStart"].startswith("2026-11-02T")     # that week's Monday
    assert out["weekEnd"].startswith("2026-11-08T")       # and its Sunday
    # Everything that is not a date must survive untouched.
    assert out["userId"] == 6507822
    assert out["jidelnaId"] == 1
    assert out["objednavky"] == {}
    # A plain-date capture keeps plain dates.
    plain = om.retarget_filter({"datumOd": "2026-08-24", "datumDo": "2026-08-30"},
                               date(2026, 11, 2), date(2026, 11, 15))
    assert plain == {"datumOd": "2026-11-02", "datumDo": "2026-11-15"}


def test_displayed_week_is_not_the_start_of_the_range():
    """The range reaches weeks either side of the week being displayed, so
    weekStart must follow the week asked about, not the range's first day."""
    day_from, day_to = date(2026, 7, 27), date(2026, 9, 6)
    body = om.filter_candidates(day_from, day_to, week_of=date(2026, 8, 26))[0]
    assert body["datumOd"].startswith("2026-07-27T")     # the wide range
    assert body["weekStart"].startswith("2026-08-24T")   # this week's Monday
    assert body["weekEnd"].startswith("2026-08-30T")


def test_required_okbase_headers_are_sent():
    """Without the x-okbase-* headers the portal is answering a different
    question — they are part of the request, not decoration."""
    session, err = om._session()
    assert session is not None, err
    assert session.headers["x-okbase-datasource"] == "defaultDataSource"
    assert session.headers["x-okbase-org-id"] == "1"
    assert session.headers["x-okbase-language"] == "cs"


def test_cookie_paste_forms():
    """Everything a person might paste into the 'Browser session' field."""
    whole = ("JSESSIONID=ABC123; okbase-remember-me=tok%3Den; "
             "_ga=GA1.2.3; cookieconsent=ok")
    jar = om.parse_cookies(whole)
    assert jar["JSESSIONID"] == "ABC123"
    # The remember-me cookie must survive: it is what outlives one session.
    assert jar["okbase-remember-me"] == "tok%3Den"
    assert len(jar) == 4

    assert om.parse_cookies("Cookie: JSESSIONID=X") == {"JSESSIONID": "X"}
    assert om.parse_cookies("JSESSIONID=X;") == {"JSESSIONID": "X"}
    assert om.parse_cookies("  X9Y8Z7  ") == {"JSESSIONID": "X9Y8Z7"}
    assert om.parse_cookies("") == {}
    assert om.parse_cookies(None) == {}


class _StubSession:
    """Just enough of requests.Session to watch what session_from_cookie does.

    `alive_after` is the path whose GET makes the sign-in start working, standing
    in for the portal handing out a new session from the single-sign-on cookie.
    """

    def __init__(self, alive_after=None, dead_status=401, raises=False):
        self.headers: dict = {}
        self.cookies = _StubJar()
        self.gets: list[str] = []
        self._alive_after = alive_after
        self._alive = False
        # `dead_status` / `raises` stand in for a portal that is not answering, as
        # opposed to one that is answering "you are signed out" — the two must
        # never be reported the same way.
        self._dead_status = dead_status
        self._raises = raises

    def get(self, url, **_kw):
        self.gets.append(url)
        if self._raises:
            raise OSError("the network is down")
        if self._alive_after and url.endswith(self._alive_after):
            self._alive = True
        return _StubResponse(200 if self._alive else self._dead_status)

    def post(self, url, **_kw):
        self.gets.append(f"POST {url}")
        return _StubResponse(401)


class _StubJar:
    def __init__(self):
        self.items: dict = {}

    def set(self, name, value, **_kw):
        self.items[name] = value

    def __iter__(self):
        return iter(_StubCookie(k, v) for k, v in self.items.items())


class _StubCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _StubResponse:
    def __init__(self, status):
        self.status_code = status
        self.text = ""


def _with_stub(stub, fn):
    """Run fn with okbase_menu._session returning `stub`."""
    original = om._session
    om._session = lambda: (stub, "")
    try:
        return fn()
    finally:
        om._session = original


COOKIES = ("JSESSIONID=DEAD; "
           "_shibsession_64656661756c74=_86aeb5005acd39d8866b4f1ad9dcf9db")


def test_a_dead_session_is_revived_through_single_sign_on():
    """Measured against the real portal: with the Shibboleth cookie still valid,
    asking the sso endpoint hands out a new session — no authenticator, no
    Microsoft. So a dead session must never be reported to the operator until
    that has been tried."""
    stub = _StubSession(alive_after="/rest/authentication/sso")
    session, err = _with_stub(stub, lambda: om.session_from_cookie(COOKIES))
    assert session is stub and err == "", err
    assert any(g.endswith("/rest/authentication/sso") for g in stub.gets), stub.gets
    # The single-sign-on cookie is what does it, so it has to be sent.
    assert any(n.startswith("_shibsession_") for n in stub.cookies.items)


def test_sso_is_tried_before_remember_me():
    stub = _StubSession(alive_after="/nothing-works")
    _with_stub(stub, lambda: om.session_from_cookie(COOKIES))
    tried = [g for g in stub.gets if "/authentication/" in g]
    assert tried, stub.gets
    assert tried[0].endswith("/rest/authentication/sso"), tried


def test_a_truly_dead_sign_in_says_so_once_everything_was_tried():
    stub = _StubSession(alive_after=None)
    session, err = _with_stub(stub, lambda: om.session_from_cookie(COOKIES))
    assert session is None
    assert err == om.SESSION_EXPIRED
    for path in om.REVIVE_PATHS:
        assert any(g.endswith(path) for g in stub.gets), (path, stub.gets)


def test_a_live_session_is_not_poked_at_all():
    stub = _StubSession()
    stub._alive = True
    session, err = _with_stub(stub, lambda: om.session_from_cookie(COOKIES))
    assert session is stub and err == ""
    assert not any("/authentication/" in g for g in stub.gets), stub.gets


CURL_BASH = (
    "curl 'https://elieric.okbase.cz/okbase/service/rest/stravovani/"
    "objednavky/nacti-vse' \\\n"
    "  -H 'accept: application/json, text/plain, */*' \\\n"
    "  -H 'content-type: application/json' \\\n"
    "  -H 'x-okbase-org-id: 1' \\\n"
    "  -b 'JSESSIONID=ABC123; _shibsession_64656661756c74=_deadbeef' \\\n"
    '  --data-raw \'{"datumOd":"2026-08-03T00:00:00.000+02:00",'
    '"jidelnaId":1,"userId":6507822,"objednavky":{}}\' \\\n'
    "  --compressed")

CURL_CMD = (
    'curl ^"https://elieric.okbase.cz/okbase/service/rest/stravovani/'
    'objednavky/nacti-vse^" ^\n'
    '  -H ^"accept: application/json^" ^\n'
    '  -b ^"JSESSIONID=ABC123; _shibsession_64656661756c74=_deadbeef^" ^\n'
    '  --data-raw ^"{^\\^"jidelnaId^\\^":1}^"')


def test_a_pasted_curl_is_read_not_mangled():
    """The 2026-09-01 failure: a Copy-as-cURL pasted into the sign-in field was
    split on semicolons into cookie names like `curl 'https://…' -H 'accept`,
    saved, and only then failed at the portal — so it looked like the sign-in was
    broken rather than the paste."""
    jar = om.parse_cookies(CURL_BASH)
    assert jar == {"JSESSIONID": "ABC123",
                   "_shibsession_64656661756c74": "_deadbeef"}, jar
    # …and the same request pasted as a plain Cookie line gives the same jar.
    assert om.parse_cookies(
        "JSESSIONID=ABC123; _shibsession_64656661756c74=_deadbeef") == jar


def test_the_windows_flavour_of_copy_as_curl_works_too():
    jar = om.parse_cookies(CURL_CMD)
    assert jar.get("JSESSIONID") == "ABC123", jar
    assert "_shibsession_64656661756c74" in jar, jar


def test_a_real_cookie_line_is_never_mistaken_for_a_command():
    """The sniff has to be narrow: a cookie value can contain almost anything."""
    for text in ("JSESSIONID=X",
                 "Cookie: JSESSIONID=X; a=b",
                 "  X9Y8Z7  ",
                 "JSESSIONID=curl; other=--header"):
        assert not om.looks_like_curl(text), text
    assert om.looks_like_curl(CURL_BASH)
    assert om.looks_like_curl(CURL_CMD)


def test_read_paste_also_recovers_the_user_and_the_request():
    """A cURL is worth more than the cookie alone — it carries who the person is,
    which canteen, and the one request body the portal accepts."""
    got = om.read_paste(CURL_BASH)
    assert not got.problem, got.problem
    assert got.user_id == "6507822"
    assert got.canteen_id == "1"
    assert isinstance(got.filter_body, dict) and "datumOd" in got.filter_body
    assert got.base == "https://elieric.okbase.cz/okbase/service"
    assert om.parse_cookies(got.cookies) == got.jar
    assert "cURL" in got.source


def test_read_paste_says_what_to_copy_instead():
    for text in ("", "hello there", "curl 'https://x/rest/y' -H 'accept: */*'"):
        got = om.read_paste(text)
        assert got.problem, text
        assert not got.cookies
    # The wording has to name the thing to click, not just refuse.
    assert "nacti-vse" in om.read_paste("hello there").problem


def test_a_portal_that_did_not_answer_is_not_an_expired_sign_in():
    """The bug this whole distinction exists for. A 502, a dropped VPN or a read
    that timed out says NOTHING about the sign-in, and reporting it as an expiry
    sent the operator off to paste a sign-in that was working perfectly."""
    for stub in (_StubSession(dead_status=502), _StubSession(raises=True)):
        session, err = _with_stub(stub, lambda s=stub: om.session_from_cookie(COOKIES))
        assert session is None
        assert om.is_unreachable(err), err
        assert not om.is_expired(err), err
        # …and no revive attempt: those paths live on the host that just failed
        # to answer, so trying them is three more timeouts for a certain nothing.
        assert not any("/authentication/" in g for g in stub.gets), stub.gets


def test_only_401_and_403_mean_signed_out():
    stub = _StubSession()
    assert _with_stub(stub, lambda: om.session_state(stub))[0] == om.SIGNED_OUT
    stub = _StubSession(dead_status=403)
    assert _with_stub(stub, lambda: om.session_state(stub))[0] == om.SIGNED_OUT
    stub = _StubSession(dead_status=500)
    assert _with_stub(stub, lambda: om.session_state(stub))[0] == om.UNREACHABLE
    stub = _StubSession()
    stub._alive = True
    assert _with_stub(stub, lambda: om.session_state(stub))[0] == om.ALIVE


def test_the_two_verdicts_are_never_the_same_string():
    assert not om.is_expired(om.PORTAL_UNREACHABLE)
    assert not om.is_unreachable(om.SESSION_EXPIRED)
    # The unreachable message carries the reason on the end, so classification
    # must not be an exact compare.
    assert om.is_unreachable(f"{om.PORTAL_UNREACHABLE} (HTTP 502)")
    assert not om.is_expired("")
    assert not om.is_unreachable("")


def test_a_renewed_cookie_line_never_loses_the_single_sign_on():
    """requests drops a cookie the server deletes, and a Shibboleth SP deletes
    _shibsession_ when it decides a session is invalid. Writing that shortened
    line back over the stored one threw away the only thing that can mint a new
    session — and the only thing a re-paste exists to supply."""
    stub = _StubSession()
    stub.cookies.set("JSESSIONID", "FRESH")      # rotated; _shibsession_ gone
    line = om.merged_cookie_header(stub, COOKIES)
    jar = om.parse_cookies(line)
    assert jar["JSESSIONID"] == "FRESH"          # the rotation is picked up
    assert any(n.startswith("_shibsession_") for n in jar), jar
    # And an empty jar must not wipe what is stored.
    assert om.merged_cookie_header(_StubSession(), COOKIES) == COOKIES


def test_the_cookie_goes_before_the_password():
    """On this site the password form cannot work (the account lives only in
    Entra ID), so trying it first spent four POSTs and four probes per attempt
    before touching the sign-in that does work."""
    stub = _StubSession()
    stub._alive = True
    settings = {"okbase_username": "someone", "okbase_password": "secret",
                "okbase_session_cookie": COOKIES}
    session, _base, _timeout, err = _with_stub(
        stub, lambda: om.open_session(settings))
    assert session is stub and err == "", err
    assert not any(g.startswith("POST") for g in stub.gets), stub.gets


def test_an_unreachable_portal_does_not_go_on_to_try_the_password():
    stub = _StubSession(raises=True)
    settings = {"okbase_username": "someone", "okbase_password": "secret",
                "okbase_session_cookie": COOKIES}
    session, _base, _timeout, err = _with_stub(
        stub, lambda: om.open_session(settings))
    assert session is None
    assert om.is_unreachable(err), err
    assert not any(g.startswith("POST") for g in stub.gets), stub.gets


def test_describe_cookies_never_shows_a_value():
    jar = om.parse_cookies(COOKIES)
    text = "\n".join(om.describe_cookies(jar))
    for value in jar.values():
        assert value not in text, text
    assert "JSESSIONID" in text and "_shibsession_" in text
    assert "the one that matters" in text        # the SSO cookie is called out


def test_describe_cookies_warns_when_the_single_sign_on_is_missing():
    lines = "\n".join(om.describe_cookies(om.parse_cookies("JSESSIONID=X")))
    assert "WARNING" in lines, lines


def test_no_session_saved_is_not_an_expiry():
    """The two must not read the same: one needs filling in, the other renewing."""
    session, err = om.session_from_cookie("")
    assert session is None
    assert err != om.SESSION_EXPIRED and "saved" in err


def test_the_sign_in_lives_with_the_user_not_with_the_build():
    """A rebuild must inherit the sign-in. It only can if the settings live in
    %APPDATA% rather than beside the exe — the program has several homes."""
    path = om.user_settings_path()
    assert path.name == "okbase.json"
    assert "Diagnostic" in str(path)
    parts = [p.lower() for p in path.parts]
    assert any("appdata" in p or "roaming" in p for p in parts), path
    # Only canteen keys are ever written there.
    assert "okbase_session_cookie" in om.OKBASE_KEYS
    assert "webex_bot_token" not in om.OKBASE_KEYS


def test_user_settings_merge_and_ignore_foreign_keys(tmp_name="okbase_test.json"):
    import okbase_menu
    from pathlib import Path
    path = Path(os.path.dirname(__file__)) / tmp_name
    original = okbase_menu.user_settings_path
    okbase_menu.user_settings_path = lambda: path
    try:
        assert om.load_user_settings() == {}          # nothing saved yet
        assert om.save_user_settings({"okbase_enabled": True,
                                      "webex_bot_token": "nope"}) == ""
        assert om.load_user_settings() == {"okbase_enabled": True}
        # A second writer must not wipe the first — the dialog saves fields
        # while a background job saves a renewed session.
        om.save_user_settings({"okbase_session_cookie": "dpapi:x"})
        assert om.load_user_settings() == {"okbase_enabled": True,
                                           "okbase_session_cookie": "dpapi:x"}
        path.write_text("{ broken", encoding="utf-8")
        assert om.load_user_settings() == {}
    finally:
        okbase_menu.user_settings_path = original
        try:
            path.unlink()
        except OSError:
            pass


def test_week_monday():
    assert om.week_monday(date(2026, 8, 24)) == MONDAY      # Monday itself
    assert om.week_monday(date(2026, 8, 30)) == MONDAY      # Sunday
    assert om.week_monday(TODAY) == MONDAY


def test_cache_age():
    assert om.cache_age_hours({}) is None
    assert om.cache_age_hours({"fetched": "nonsense"}) is None
    age = om.cache_age_hours(cache_of(NESTED, datetime(2026, 8, 26, 3, 15)), NOW)
    assert abs(age - 6.0) < 0.01, age


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
