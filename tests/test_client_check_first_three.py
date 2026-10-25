"""
Offline tests for the "first three businesses named" column of the Namebeam Client Check
(scripts/client_check.py first_three_named). No network, no keys. Runs under pytest or directly:

    python -I -X utf8 -m pytest tests/test_client_check_first_three.py -q
    python -I -X utf8 tests/test_client_check_first_three.py

Fixture: tests/fixtures/client_check_first_three/dryrun1024_answers.json holds the 12 real answers of
the 2026-10-24 dry run (client "Coolray Heating and Air", question "best HVAC company in Atlanta for a
furnace replacement"), copied from status/client_checks/2026-10-25/DRYRUN1024-01_*.json, each with the
column value the old code wrote (before_first_three_named).

ORACLE below is a human read of each answer (written 2026-10-24 CT before the fix): the local service
businesses a careful reader would list first, in order of appearance. Equipment manufacturers listed as
brands (Carrier, Trane, Lennox, Rheem...), headings, place names and generic nouns are not businesses.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import client_check as cc  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "client_check_first_three", "dryrun1024_answers.json")
ALIASES = ["Coolray"]

ORACLE = {
    # Anthropic: "Carrier/Day & Night" and "Lennox dealers" are manufacturers; only two local businesses are named.
    "DRYRUN1024-01_anthropic.json": ["American Home Comfort", "Moncrief Air Conditioning"],
    "DRYRUN1024-01_anthropic_run2.json": ["Moncrief Heating & Air", "Comfort Systems USA", "Estes Services"],
    "DRYRUN1024-01_anthropic_run3.json": ["Moncrief Heating & Air", "Comfort Systems USA",
                                          "One Hour Heating & Air Conditioning"],
    # Gemini: the businesses are numbered "### 1. Name" headings.
    "DRYRUN1024-01_gemini.json": ["Conditioned Air Systems", "Cool Air Design", "Estes Services"],
    "DRYRUN1024-01_gemini_run2.json": ["Reliable Heating & Air", "Cool Air Design", "Estes Services"],
    "DRYRUN1024-01_gemini_run3.json": ["Reliable Heating & Air", "Cool Air Mechanical", "Conditioned Air Systems"],
    # OpenAI runs 1 and 2 name no business (brands and advice only). Run 3 names the client first, as "Coolray".
    "DRYRUN1024-01_openai.json": [],
    "DRYRUN1024-01_openai_run2.json": [],
    "DRYRUN1024-01_openai_run3.json": ["Coolray", "One Hour Heating & Air", "Service Experts"],
    # Perplexity
    "DRYRUN1024-01_perplexity.json": ["PV Heating, Cooling & Plumbing", "Moncrief Heating & Air",
                                      "The Air Company of GA"],
    "DRYRUN1024-01_perplexity_run2.json": ["The Air Company", "PV Heating, Cooling & Plumbing", "Bardi"],
    "DRYRUN1024-01_perplexity_run3.json": ["The Air Company of Georgia", "PV Heating, Cooling & Plumbing",
                                           "Estes Services"],
}

# What the old code wrote for these files was junk (kept here so the before/after is reviewable).
JUNK_BEFORE = {"Atlanta. Its", "Metro Atlanta", "Carrier Factory Authorized Dealer", "Air Conditioning",
               "Furnace Replacement", "Atlanta. They", "Homeowners", "Atlanta for Furnace Replacement",
               "Well-Established Companies", "Carrier/Day & Night", "Metro Atlanta. They"}


def _rows():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def _run(r, aliases=ALIASES):
    return cc.first_three_named(r["answer_verbatim"], r["sources_cited"], r["market"], r["date_utc"],
                                r["client"], aliases)


def test_fixture_covers_all_twelve_answers():
    rows = _rows()
    assert len(rows) == 12 and {r["file"] for r in rows} == set(ORACLE)


def test_twelve_answers_match_the_oracle():
    for r in _rows():
        got = _run(r)
        assert got == ORACLE[r["file"]], (r["file"], got, ORACLE[r["file"]])


def test_old_junk_is_gone_from_every_answer():
    for r in _rows():
        got = _run(r)
        assert not (set(got) & JUNK_BEFORE), (r["file"], got)
        for n in got:
            assert n.lower() in r["answer_verbatim"].lower(), "invented name %r in %s" % (n, r["file"])


def test_no_manufacturer_place_or_fragment_is_returned():
    banned = re.compile(r"^(?:carrier|trane|lennox|rheem|goodman|bryant|american standard)\b[^&]*$", re.I)
    for r in _rows():
        for n in _run(r):
            assert not banned.match(n), (r["file"], n)
            assert not re.search(r"[.!?]\s", n), (r["file"], n)
            assert not n.lower().startswith(("atlanta ", "metro ")) or "Heating" in n or "Air" in n


def test_openai_run3_client_is_listed_only_with_alias_or_name():
    r = [x for x in _rows() if x["file"] == "DRYRUN1024-01_openai_run3.json"][0]
    assert _run(r)[0] == "Coolray"                      # alias given: the named client shows in position
    no_alias = _run(r, aliases=None)                    # no alias: "Coolray" is not the full client name
    assert no_alias == ["One Hour Heating & Air", "Service Experts", "Aire Serv"]


def test_client_full_name_is_listed_in_position():
    text = "**Zed Roofing** first, then **Acme Roofing** and **Orbit Roofing**.\n"
    got = cc.first_three_named(text, [], "Springfield, MO", "2026-10-27", "Acme Roofing", None)
    assert got == ["Zed Roofing", "Acme Roofing", "Orbit Roofing"]


def test_dealer_names_kept_and_brand_lists_dropped():
    text = ("Brands: **Carrier**, **Trane**, **Lennox**.\n"
            "Local picks: **Carrier Heating & Air**, **Smith Plumbing**, and **Rheem/Ruud**.\n")
    got = cc.first_three_named(text, [], "Atlanta, GA", "2026-10-27")
    assert got == ["Carrier Heating & Air", "Smith Plumbing"], got


def test_city_start_dropped_only_without_a_business_word():
    text = "**Atlanta for Furnace Replacement** is a heading. **Atlanta Air Care** is a company. **Metro Atlanta**.\n"
    got = cc.first_three_named(text, [], "Atlanta, GA", "2026-10-27")
    assert got == ["Atlanta Air Care"], got


def test_law_firm_style_names_survive():
    text = "**Crowe & Dunlevy** leads. Also **Doerner Saunders Daniel & Anderson** and **McAfee & Taft**.\n"
    got = cc.first_three_named(text, [], "Oklahoma City, OK", "2026-10-27")
    assert got == ["Crowe & Dunlevy", "Doerner Saunders Daniel & Anderson", "McAfee & Taft"], got


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if fails else 0)
