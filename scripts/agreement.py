"""
ENGINE AGREEMENT (DW-1), computed automatically on every run.

Why this exists: on 2026-08-05 the first two-engine comparison was done by
hand, in a session, and it took three attempts to get the metric right. That
work must not have to be redone. This module encodes the CORRECT metric so
every future run produces the finding as a byproduct.

THE METRIC, and why it is not set overlap:
Gemini writes 4,279 characters per answer to Perplexity's 1,920. When one
engine names 27 companies and the other names 8, PERFECT agreement on all 8
still caps Jaccard overlap near 30 percent. Set overlap measures verbosity,
not agreement. Two published-looking figures, 2.5 and 4.2 percent, were
produced that way and both were discarded.

The correct measure is DIRECTIONAL CONTAINMENT: of the companies the TERSER
engine named, what share did the other engine also name? It never penalises
an engine for saying more, so it is valid across sources of different length.

Entity matching is mechanical (canonical forms, whole-word containment, a
length guard) so two runs over the same text always agree. Extraction is
ORTHOGRAPHIC, keyed on capitalisation rather than bolding, because engines
bold to their own taste and bolding is not comparable across them.
"""

import itertools

from extract_entities_orthographic import extract_entities_orthographic
from extract_named_entities import same_entity


def _names(row):
    return extract_entities_orthographic(
        row.get("answer_verbatim", ""),
        row.get("business"),
        None,
        row.get("sources_cited"),
    )


def containment(a_names, b_names):
    """Share of a_names that also appear in b_names. Returns (pct, hits, n)."""
    if not a_names:
        return None, 0, 0
    hits = sum(1 for a in a_names if any(same_entity(a, b) for b in b_names))
    return round(100.0 * hits / len(a_names), 1), hits, len(a_names)


def pair_agreement(row_a, row_b):
    """Compare two OK engine rows for the same business and date."""
    na, nb = _names(row_a), _names(row_b)
    # Direction is chosen by which engine was terser, never by engine name,
    # so the measure cannot be gamed by whoever happens to be verbose today.
    if len(na) <= len(nb):
        terser, verbose, tn, vn = row_a, row_b, na, nb
    else:
        terser, verbose, tn, vn = row_b, row_a, nb, na
    pct, hits, n = containment(tn, vn)
    return {
        "engine_a": row_a.get("engine"),
        "engine_b": row_b.get("engine"),
        "named_by_a": bool(row_a.get("business_mentioned")),
        "named_by_b": bool(row_b.get("business_mentioned")),
        "naming_disagreement": bool(row_a.get("business_mentioned"))
                               != bool(row_b.get("business_mentioned")),
        "terser_engine": terser.get("engine"),
        "terser_entity_count": len(tn),
        "verbose_entity_count": len(vn),
        "containment_pct": pct,
        "shared_count": hits,
        "answer_chars_a": len(row_a.get("answer_verbatim") or ""),
        "answer_chars_b": len(row_b.get("answer_verbatim") or ""),
        "zero_shared": hits == 0 and n > 0,
    }


def build_agreement_row(business_row, date_utc, engine_rows):
    """engine_rows: list of OK rows for one business on one date."""
    usable = [r for r in engine_rows
              if r.get("run_status") == "OK" and r.get("answer_verbatim")]
    pairs = []
    for a, b in itertools.combinations(usable, 2):
        if a.get("engine") == b.get("engine"):
            continue
        pairs.append(pair_agreement(a, b))
    return {
        "check_id": None,  # set by the caller
        "date_utc": date_utc,
        "business": business_row["business"],
        "row_type": "agreement",
        "prompt_text": business_row["prompt_1"],
        "engines_compared": sorted({r.get("engine") for r in usable}),
        "engines_usable": len(usable),
        "pairs": pairs,
        "method_note": (
            "Directional containment, NOT set overlap. Set overlap has a "
            "ceiling set by verbosity: if one engine names 27 companies and "
            "the other names 8, perfect agreement still caps Jaccard near 30 "
            "percent. Entities are extracted ORTHOGRAPHICALLY (capitalisation), "
            "not from bolding, because engines bold to their own taste. "
            "Matching is mechanical and reproducible; errors run toward "
            "under-counting agreement, so the true figure is more likely "
            "higher than reported than lower."),
    }
