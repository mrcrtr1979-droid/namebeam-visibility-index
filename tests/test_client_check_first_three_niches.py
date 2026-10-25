"""
Offline tests for the "first three businesses named" column across niches other than the HVAC question
it was first tuned on (scripts/client_check.py first_three_named). No network, no keys. Runs under
pytest or directly:

    python -I -X utf8 -m pytest tests/test_client_check_first_three_niches.py -q
    python -I -X utf8 tests/test_client_check_first_three_niches.py

Fixture: tests/fixtures/client_check_first_three/niches_answers.json holds minimal excerpts (the first
lines of the answer, cut where the value stops changing) of real Client Check answers from
status/client_checks/2026-10-23*/ and 2026-10-24/. One answer per niche that names businesses, plus one
per niche where an engine named none (advice, headings and directories only: the right value is []).
"expected" is a human read: businesses only, in order of appearance; no places, headings, generic
phrases, directories or rating bodies. The rest of the file pins the structural rules with tiny texts.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import client_check as cc  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "client_check_first_three", "niches_answers.json")


def _rows():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def _run(r):
    return cc.first_three_named(r["answer_excerpt"], [], r["market"], r["date_utc"], r["client"], None)


def test_fixture_covers_nineteen_niches_with_and_without_names():
    rows = _rows()
    named = {r["niche"] for r in rows if r["expected"]}
    empty = {r["niche"] for r in rows if not r["expected"]}
    assert len(named) == 19, sorted(named)
    assert len(empty) >= 10 and all(n.endswith("(names no business)") for n in empty)


def test_every_niche_excerpt_matches_the_human_read():
    for r in _rows():
        got = _run(r)
        assert got == r["expected"], (r["file"], r["niche"], got, r["expected"])


def test_returned_names_are_always_in_the_answer_text():
    for r in _rows():
        low = " ".join(r["answer_excerpt"].replace("\u2019", "'").lower().split())
        for n in _run(r):
            assert " ".join(n.replace("\u2019", "'").lower().split()) in low, (r["file"], n)


def test_field_labels_and_advice_headings_are_not_businesses():
    text = ("1. Ask for references\n- **Certifications**: licensed\n- **Licensing**: required\n"
            "**Best for:** families\n**Best for:** seniors\n")
    assert cc.first_three_named(text, [], "Austin, TX", "2026-10-27") == []


def test_city_or_area_headings_are_not_businesses():
    text = "## **Sioux Falls**\n- Largest city\n## **Deadwood**\n- Historic\n**Near I-77** is busy.\n"
    assert cc.first_three_named(text, [], "Rapid City, SD", "2026-10-27") == []


def test_street_addresses_and_state_tails_are_dropped():
    text = "- **Raitz Automotive Service**: 604 S. Ridge Ave\n- **Steve's Service Center** - Kannapolis\n**HVAC Post Falls ID**\n"
    got = cc.first_three_named(text, [], "Kannapolis, NC", "2026-10-27")
    assert got == ["Raitz Automotive Service", "Steve's Service Center"], got


def test_numbered_heading_with_firm_keeps_the_firm_not_the_lawyer():
    text = ("### 1. **Affectionate Child Dentistry**\n* **Dr. Anita Vidyasagar**\n* **Best for:** kids\n"
            "### 2. **Superstition Pediatric Dentistry**\n* **Dr. Paul Downey / Dr. Richard Olsen**\n")
    got = cc.first_three_named(text, [], "Mesa, AZ", "2026-10-27")
    assert got == ["Affectionate Child Dentistry", "Superstition Pediatric Dentistry"], got


def test_category_heading_over_a_list_of_firms_is_dropped():
    text = ("### 1. Corporate, Business, & Litigation (Full-Service Big Firms)\n"
            "*   **Crowe & Dunlevy**\n*   **Fellers Snider**\n")
    got = cc.first_three_named(text, [], "Oklahoma City, OK", "2026-10-27")
    assert got == ["Crowe & Dunlevy", "Fellers Snider"], got


def test_lawyer_named_alone_is_listed_but_a_lawyer_inside_a_firm_bullet_is_not():
    alone = "A shortlist includes **Mark Rosenfeld**, who handles DUI, and **Louis J. Shapiro**, a specialist.\n"
    assert cc.first_three_named(alone, [], "Los Angeles, CA", "2026-10-27") == ["Mark Rosenfeld", "Louis J. Shapiro"]
    firm = "- **Goranson Bain Ausley** - Family law. **Rob Frazer** is noted for property division.\n"
    assert cc.first_three_named(firm, [], "Austin, TX", "2026-10-27") == ["Goranson Bain Ausley"]


def test_names_with_digits_commas_and_trades_stay_whole():
    text = ("- **10 Barrel Brewing** - pub\n- **PV Heating, Cooling & Plumbing** - hvac\n"
            "- **Beasley, Allen, Crow, Methvin, Portis & Miles** - firm\n")
    got = cc.first_three_named(text, [], "Bend, OR", "2026-10-27")
    assert got == ["10 Barrel Brewing", "PV Heating, Cooling & Plumbing",
                   "Beasley, Allen, Crow, Methvin, Portis & Miles"], got


def test_national_chains_in_brackets_are_listed_but_brands_are_not():
    text = "- National chains (Firestone, Midas, Jiffy Lube)\n- Compare equipment brands (Carrier, Lennox, Trane)\n"
    got = cc.first_three_named(text, [], "Kannapolis, NC", "2026-10-27")
    assert got == ["Firestone", "Midas", "Jiffy Lube"], got


def test_markdown_escapes_and_model_artifacts_are_cleaned():
    text = "<bos>- **Dumar \\& Son Construction** - remodeling\n- **Sunset Builders** - decks\n"
    got = cc.first_three_named(text, [], "Olympia, WA", "2026-10-27")
    assert got == ["Dumar & Son Construction", "Sunset Builders"], got


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
