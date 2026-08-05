"""
E1 NAMED-ENTITY EXTRACTOR (schema v3 field: competitors_mentioned)

Plain-language note for a non-coder reading along:
scripts/e1_runner.py has always stored the engine's full answer in the
field `answer_verbatim`, but it wrote `competitors_mentioned: []` on every
row with a note saying extraction was "deferred to the WORK layer". It was
never done. This module does it, and because answer_verbatim was stored all
along, EVERY historical row can be backfilled with zero new API spend.

Why this matters (doctrine 100, two returns from one probe): once this field
is populated, every check that runs also becomes DW-1 data, the disagreement
constant, and competitor tracking, at no extra cost.

Method, stated plainly so it can be audited:
- These models mark named entities with **bold**. We take bolded spans as
  candidates. That is the observable signal, not a guess about meaning.
- We then reject anything that is plainly not an entity: pure numbers or
  prices, sentence fragments and questions, list-label boilerplate
  ("Best overall"), the subject business itself, and spans with no capital
  letter.
- We do NOT use an LLM to decide. The rule is mechanical and reproducible,
  so two runs over the same text always give the same answer. A judgment
  call that cannot be reproduced does not belong in the corpus.

Known limitation, printed rather than hidden: an engine that names a
competitor WITHOUT bolding it will be missed by this extractor. That is a
false negative, and a false negative is the safe direction of error for a
corpus whose whole value is that it never overstates. Do not "fix" this by
loosening the rule until the extractor starts inventing entities.
"""

import re

# Boilerplate list-labels and rubric phrases these models emit in bold.
# Matched case-insensitively against the whole span.
_LABEL_PATTERNS = [
    r"^best\b.*", r"^lowest[- ]cost\b.*", r"^free\b.*", r"^broader\b.*",
    r"^cheapest\b.*", r"^top\b.*", r"^most\b.*", r"^ideal\b.*",
    r"^recommended\b.*", r"^why\b.*", r"^how\b.*", r"^what\b.*",
    r"^who\b.*", r"^when\b.*", r"^if\b.*", r"^want\b.*", r"^need\b.*",
    r"^note\b.*", r"^important\b.*", r"^summary\b.*", r"^bottom line\b.*",
    r"^key takeaway\b.*", r"^pros?\b$", r"^cons?\b$", r"^tip\b.*",
    r"^single\b.*", r"^ongoing\b.*", r"^practical\b.*", r"^choose\b.*",
    r"^consider\b.*", r"^avoid\b.*", r"^overall\b.*", r"^verdict\b.*",
]

# Generic category nouns that are descriptions, not businesses.
_GENERIC = {
    "small business", "small businesses", "ai", "seo", "aeo", "geo",
    "chatgpt", "perplexity", "gemini", "claude", "copilot",
    "google", "google ai overview", "bing",
    "experience", "reviews", "pricing", "cost", "free tier",
    "bbb accreditation", "bbb status", "bbb", "local address/phone",
    "aca subsidies", "licensed", "insured",
}


def _is_label(span_lower):
    for pat in _LABEL_PATTERNS:
        if re.match(pat, span_lower):
            return True
    return False


def extract_named_entities(answer_text, subject_business=None,
                           subject_variants=None):
    """Return a de-duplicated, order-preserving list of entities the engine
    named in `answer_text`, excluding the subject business itself.

    Returns [] for empty or missing text. Never raises on odd input.
    """
    if not answer_text:
        return []

    subject_forms = set()
    if subject_business:
        subject_forms.add(subject_business.strip().lower())
    for v in (subject_variants or []):
        if v:
            subject_forms.add(v.strip().lower())

    candidates = re.findall(r"\*\*(.+?)\*\*", answer_text, flags=re.S)

    out = []
    for raw in candidates:
        span = raw.strip()
        # strip trailing punctuation and surrounding quotes
        span = span.strip().strip(":;,.").strip().strip('"').strip("'")
        span = span.replace("“", "").replace("”", "").strip()
        if not span:
            continue

        low = span.lower()

        # reject: too short / too long / sentence-like
        if len(span) < 3 or len(span) > 60:
            continue
        if span.count(" ") > 6:
            continue
        if span.endswith("?"):
            continue

        # reject: must BEGIN with a capital. Entities do; descriptive
        # phrases like "reliable-looking contractors in Joplin" do not.
        if not span[0].isupper():
            continue

        # reject: compound spans ("AI Rank Checker or Surfeo") are two
        # entities glued together; taking either one would be a guess.
        if " or " in low or " and/or " in low:
            continue

        # reject: clause-like spans carrying a verb or pronoun. These are
        # the engine stating a CRITERION ("Whether they are licensed and
        # insured"), which is valuable but is not a named entity. Criteria
        # are captured separately by extract_stated_criteria below.
        if re.search(r"\b(are|is|was|were|they|you|your|their|its|has|have)\b", low):
            continue

        # reject: pure numbers, prices, percentages
        if re.fullmatch(r"[^A-Za-z]+", span):
            continue
        if span.startswith("$"):
            continue

        # reject: rubric labels and generic category nouns
        if _is_label(low):
            continue
        if low in _GENERIC:
            continue

        # reject: the subject business itself, that is business_mentioned
        if low in subject_forms:
            continue
        if any(f and f in low for f in subject_forms if len(f) > 4):
            continue

        if span not in out:
            out.append(span)

    return out


# ---------------------------------------------------------------------------
# SECOND RETURN FROM THE SAME PROBE (doctrine 108)
# ---------------------------------------------------------------------------
def extract_stated_criteria(answer_text):
    """The engine often states, in its own words, WHAT IT WEIGHED before
    naming anyone. On the 2026-08-04 Joplin contractor check it explicitly
    listed BBB accreditation, BBB status, experience, and a local address
    and phone. That is a per-vertical fix list written by the engine itself.

    A vendor that reduces an answer to a score throws this away on every
    run. We keep it. Returns a list of bolded clause-like spans, which is
    exactly what extract_named_entities rejects, so the two functions
    partition the bolded spans between them and nothing is lost.
    """
    if not answer_text:
        return []
    out = []
    for raw in re.findall(r"\*\*(.+?)\*\*", answer_text, flags=re.S):
        span = raw.strip().strip(":;,.").strip()
        if not span or len(span) > 80:
            continue
        low = span.lower()
        clause = re.search(r"\b(are|is|was|were|they|you|your|their|its|has|have|whether)\b", low)
        if clause and span not in out:
            out.append(span)
    return out


# ---------------------------------------------------------------------------
# ENTITY RESOLUTION (added 2026-08-05)
#
# Why this exists: the first two-engine run reported a 2.5 percent overlap in
# the brands Perplexity and Gemini named. That number was NOT publishable,
# because the extractor keys on bolded spans and Gemini bolds two to three
# times more entities than Perplexity, and because exact string matching
# treats "Kennedy Painting" and "Kennedy Painting LLC" as different companies.
# Normalizing alone moved the figure from 2.5 to 4.7 percent, which proved
# the method was inside the result.
#
# canonical() strips what does not identify a company. same_entity() then
# matches on containment, because engines routinely give one a legal suffix
# and the other not. Both are mechanical and reproducible: no LLM, no
# judgement call, so two runs over the same text always agree.
# ---------------------------------------------------------------------------

_LEGAL_SUFFIXES = (
    "llc", "l.l.c", "inc", "incorporated", "corp", "corporation", "co",
    "company", "ltd", "limited", "plc", "llp", "lp", "pllc", "pc", "gmbh",
)
_LEADING_NOISE = ("the", "a", "an")


def canonical(name):
    """Reduce a company name to what actually identifies it. Returns '' if
    nothing identifying survives, and the caller must drop those."""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[\u2018\u2019\u201c\u201d']", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    words = [w for w in s.split() if w]
    while words and words[0] in _LEADING_NOISE:
        words.pop(0)
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def same_entity(a, b):
    """True if two names plausibly denote the same company.

    Containment, not equality, because one engine writes 'Kennedy Painting'
    and the other 'Kennedy Painting LLC'. Guarded by a minimum length so
    short generic tokens cannot swallow unrelated names: 'AI' must never
    match 'AI Rank Checker'.
    """
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if len(shorter) < 6:
        return False
    # Require a whole-word boundary so "paint" does not match "painting pros".
    return re.search(r"\b" + re.escape(shorter) + r"\b", longer) is not None


def resolve_overlap(list_a, list_b):
    """Return (shared, only_a, only_b) using entity resolution rather than
    exact strings. This is what any published brand-overlap figure must use."""
    a = [x for x in (list_a or []) if canonical(x)]
    b = [x for x in (list_b or []) if canonical(x)]
    shared, used_b = [], set()
    for x in a:
        for i, y in enumerate(b):
            if i in used_b:
                continue
            if same_entity(x, y):
                shared.append(x)
                used_b.add(i)
                break
    only_a = [x for x in a if x not in shared]
    only_b = [y for i, y in enumerate(b) if i not in used_b]
    return shared, only_a, only_b
