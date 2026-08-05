"""
BOLD-INDEPENDENT ENTITY EXTRACTION (added 2026-08-05).

Why this exists, stated plainly so nobody removes it later:
The original extractor keys on **bolded** spans. That worked, but it made
cross-engine comparison invalid, because engines bold to their own taste.
Measured on the 2026-08-05 corpus: Gemini bolds 15 to 21 entities per answer
while Perplexity bolds 2 to 12. Any "the engines named different companies"
figure built on bolding is partly measuring typography.

This module keys on ORTHOGRAPHY instead: runs of Capitalised words, which is
a convention both engines follow because English requires it. It is therefore
comparable ACROSS engines in a way bolding is not.

Still mechanical and reproducible. No LLM, no judgement call, so two runs over
the same text always agree. Errors run toward FALSE NEGATIVES, which is the
safe direction for a corpus whose value is that it never overstates.

The hard part is sentence-initial capitalisation: "Several painting companies"
starts a sentence and is not a company. Handled by requiring a candidate to
clear at least one of four bars:
  1. it is multi-word Title Case and not sentence-initial, or
  2. it is sentence-initial BUT also appears elsewhere in the text
     non-initially, which is strong evidence it is a real name, or
  3. its canonical form matches a domain the engine actually cited, which is
     the strongest evidence available and is engine-independent, or
  4. it is a single word, mid-sentence, that looks like a brand on its own
     orthography: internal capitalisation (ZipTie), a dot or digit inside
     (Otterly.AI), or repetition. Bar 4 exists because bars 1 to 3 admitted
     single-word names ONLY via bar 3, which silently biased against any
     engine that returns no citation list. That bias produced a measured
     false zero on 2026-08-05 and is documented at the bar itself.
"""

import re

# Words that are capitalised at sentence start but are never company names.
_STOP_INITIAL = {
    "the", "a", "an", "this", "that", "these", "those", "there", "here",
    "it", "its", "he", "she", "they", "we", "you", "i", "if", "when",
    "while", "for", "and", "but", "or", "so", "because", "however",
    "several", "many", "most", "some", "both", "each", "every", "all",
    "based", "given", "considering", "depending", "according", "overall",
    "generally", "typically", "usually", "often", "note", "important",
    "best", "top", "other", "another", "first", "second", "third", "finally",
    "additionally", "furthermore", "moreover", "meanwhile", "therefore",
    "unfortunately", "fortunately", "ultimately", "however", "although",
    "determining", "choosing", "finding", "looking", "using", "here's",
    "what", "which", "who", "how", "why", "where", "yes", "no",
    # Added 2026-08-05: "Do AI engines recommend my business" was being
    # admitted as the entity "Do AI" on a real corpus row.
    "do", "does", "did", "can", "should", "would", "will", "are", "is",
}

# Generic capitalised nouns that are categories, not companies.
_GENERIC = {
    "ai", "seo", "aeo", "geo", "llm", "faq", "usa", "us", "uk",
    "google", "chatgpt", "perplexity", "gemini", "claude", "copilot", "grok",
    "google ai overview", "ai overview", "bing", "yandex",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "bbb", "better business bureau",
}

_CAP_RUN = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’.\-]*(?:\s+(?:of|the|and|for|de|la)\s+"
    r"[A-Z][A-Za-z0-9&'’.\-]*|\s+[A-Z][A-Za-z0-9&'’.\-]*){0,5})"
)


def _sentence_starts(text):
    """Character offsets that begin a sentence, so we can tell a real name
    from a capitalised first word."""
    starts = {0}
    for m in re.finditer(r"(?<=[.!?:;])\s+|\n+|^\s*[-*•]\s*", text, re.M):
        starts.add(m.end())
    return starts


def _domain_tokens(sources):
    """Canonical word-sets from cited domains. Engine-independent evidence
    that a name is a real organisation."""
    out = set()
    for u in sources or []:
        m = re.search(r"https?://([^/]+)", u or "")
        if not m:
            continue
        host = m.group(1).lower().replace("www.", "")
        core = host.rsplit(".", 1)[0]
        core = re.sub(r"\.(co|com|org|net|gov|edu|ac)$", "", core)
        for piece in re.split(r"[.\-]", core):
            if len(piece) >= 4:
                out.add(piece)
    return out


def extract_entities_orthographic(text, subject=None, subject_variants=None,
                                  sources_cited=None):
    """Return entity names found by capitalisation, not by bolding."""
    if not text:
        return []

    subj = set()
    for s in [subject] + list(subject_variants or []):
        if s:
            subj.add(re.sub(r"[^a-z0-9]+", "", s.lower()))

    starts = _sentence_starts(text)
    dom = _domain_tokens(sources_cited)

    seen_noninitial = set()
    noninitial_counts = {}
    raw = []
    for m in _CAP_RUN.finditer(text):
        span = m.group(1).strip().strip(".,;:")
        if not span:
            continue
        key = span.lower()
        initial = m.start() in starts
        if not initial:
            seen_noninitial.add(key)
            noninitial_counts[key] = noninitial_counts.get(key, 0) + 1
        raw.append((span, key, initial))

    out = []
    for span, key, initial in raw:
        words = span.split()
        flat = re.sub(r"[^a-z0-9]+", "", key)
        if not flat or flat in subj:
            continue
        if any(flat and flat in s for s in subj if len(flat) > 4):
            continue
        if key in _GENERIC or words[0].lower() in _STOP_INITIAL and len(words) == 1:
            continue
        if len(span) < 3 or len(words) > 6:
            continue

        domain_backed = any(t in flat for t in dom if len(t) >= 5)

        # The three bars. Any one is enough.
        ok = False
        if len(words) >= 2 and not initial and words[0].lower() not in _STOP_INITIAL:
            ok = True
        elif initial and key in seen_noninitial:
            ok = True
        elif domain_backed:
            ok = True

        # FOURTH BAR, added 2026-08-05 after a measured false zero.
        #
        # The three bars above admit a single-word name ONLY when a cited
        # domain happens to back it. That is a silent bias against any engine
        # that returns no citations. Gemini's plain generateContent returns
        # none, so on the 2026-08-05 namebeam pair the extractor found 0
        # shared entities between two answers that BOTH named ZipTie: one
        # wrote "ZipTie", the other "ZipTie.dev", and neither survived. The
        # run then published containment 0.0 percent, which is not a finding,
        # it is a broken measurement (doctrine 122).
        #
        # A single word earns admission on its own orthography, which is
        # engine-independent evidence and needs no citation list:
        #   internal capitalisation  ZipTie, HubSpot, BrightLocal, SEMrush
        #   a dot or digit inside    Otterly.AI, ZipTie.dev, Web2
        #   repetition mid-sentence  a real name gets used more than once
        if not ok and len(words) == 1 and not initial:
            camel = re.match(r"^[A-Z][a-z0-9]*[A-Z]", span) is not None
            dotted = re.search(r"[.\d]", span) is not None
            repeated = noninitial_counts.get(key, 0) >= 2
            # A bare short acronym is a category, not a company: SMB, ROI, CRM.
            bare_acronym = span.isupper() and len(span) <= 4
            if (camel or dotted or repeated) and not bare_acronym:
                ok = True

        if not ok:
            continue
        if span not in out:
            out.append(span)
    return out
