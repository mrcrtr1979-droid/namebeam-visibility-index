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
comparable ACROSS engines in a way bolding is not, not by bolding.

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


# --------------------------------------------------------------------------
# STOPLIST, added per WO-2026-09-06-A / WO-2026-09-06-C.
#
# Measured on the live corpus 2026-09-06: across 4,096 OK API answers this
# extractor produced 10,611 distinct strings, and a real share were not
# businesses at all -- rubric labels ("Summary Recommendation"), section
# headers, regulatory/certification bodies ("State Bar of California",
# "GAF Master Elite"), and bare category acronyms ("SaaS", "HIPAA", "RevPAR")
# that pass the orthographic bars (internal capitalisation, digits, repeat
# use) without being a company. WO-2026-09-06-A named five live examples by
# hand: "Parking", "Hot Tub", "Pro Tip", "Highway 1 Traffic", "For Beach +
# Nightlife/Dining". This set was built by frequency-sorting the actual
# extractor output over the whole corpus and hand-classifying the top ~400
# strings, not by guessing categories in the abstract.
#
# Exact match, case-insensitive, against the full candidate span -- never a
# substring match -- so a real business that merely CONTAINS a stopword
# ("Local Motion Painting LLC") is untouched. This is deliberately narrower
# than it could be: known residual junk this list does NOT catch (measured,
# not hidden) is one-off amenity/section headers unique to a single answer
# (a name seen once cannot be told apart from a stoplist entry seen once
# without eating real single-mention names), sentence-run-on artifacts where
# the run-matcher glues a sentence-final capitalised word to the next
# sentence's opener across a period, and bare geographic place names
# ("St. Louis", "Kansas City"), which are real names but not competing
# BUSINESSES -- left untouched here because that is a different, larger
# policy question (should the corpus even try to classify entity TYPE) that
# was not asked for in this work order and deserves its own decision, not a
# silent stoplist entry.
# --------------------------------------------------------------------------
_STOPLIST = {
    # --- WO-2026-09-06-A named examples, verbatim ---
    "parking", "hot tub", "pro tip", "highway 1 traffic",
    "for beach + nightlife/dining", "state bar of california",
    "local bar associations", "questions to ask",
    # --- generic single words / rubric labels (high-frequency, non-business) ---
    "ask", "look", "check", "choose", "step", "part", "local", "downtown",
    "overview", "specialty", "specialties", "reputation", "marketplace",
    "location", "english", "focus", "read", "get", "search", "course",
    "good", "known", "highlights", "free", "pricing", "marketing",
    "construction", "heating", "plumbing", "hotel", "crypto", "transparent",
    "pros", "cons", "platform", "wifi", "chateau", "south", "westside",
    "broadway", "hospitable",
    # --- multi-word rubric / section-header phrases ---
    "summary recommendation", "key factors", "look for",
    "recommended next steps", "core business model", "pick one",
    "good host", "premier host", "trial experience", "occupancy rate",
    "market analysis", "key questions", "operating expenses",
    "specific recommendations", "essential steps", "monthly profit",
    "monthly rent", "web design", "local seo", "popular platforms",
    "average daily rate", "health insurance broker", "find local help",
    "find the right broker", "red flags", "monitoring tools",
    "market dashboards", "key tools", "key features", "board certified",
    "board certification", "industry experience",
    "revenue per available room", "my honest take", "local facebook",
    "check google", "personal injury trial law",
    # --- acronyms / category shorthand mistaken for brand names ---
    "kpis", "saas", "hipaa", "hipaa-compliant", "smbs", "revpar", "adr",
    "strs", "sops", "llms", "defi", "dsps", "b2b saas", "ctv-focused",
    "ctv-specific", "ai-driven", "ai-powered", "ai-enabled", "ai-generated",
    "ugc-style",
    # --- regulatory / certification bodies (real orgs, not competitors) ---
    "state bar of texas", "alameda county bar association",
    "national association of health underwriters",
    "arizona registrar of contractors",
    "texas department of licensing and regulation",
    "texas board of legal specialization", "missouri department of insurance",
    "florida dbpr", "north american technician excellence",
    "nate-certified", "gaf master elite", "certainteed select shinglemaster",
    "owens corning platinum preferred", "owens corning preferred",
    "manual j", "fmcsa",
}

# Pronoun-contraction artifact: the run-matcher admits "I" (capital) glued to
# a trailing contraction ("I'll", "I'd", "I've", "I'm") because the regex
# treats the apostrophe as a normal name character (needed for "O'Brien",
# "Trader Joe's"). Caught by pattern, not by listing every contraction.
_CONTRACTION_ARTIFACT = re.compile(r"^i[''](ll|d|m|ve|s)$", re.I)

_CAP_RUN = re.compile(
    r"\b([A-Z][A-Za-z0-9&''.-]*(?:\s+(?:of|the|and|for|de|la)\s+"
    r"[A-Z][A-Za-z0-9&''.-]*|\s+[A-Z][A-Za-z0-9&''.-]*){0,5})"
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
        for piece in re.split(r"[.-]", core):
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
        # Sentence/paragraph run-on artifact: the \s+ connector between
        # capitalised words matches ACROSS a literal newline, so a real name
        # that ends a paragraph gets glued to the next paragraph's opening
        # capitalised word ("VRBO\n\nWhen", "MO\n\nI"). TRUNCATE to the
        # text before the first newline rather than discarding the whole
        # span outright: measured on the 2026-09-06 corpus, outright
        # rejection was silently dropping the ONLY mention of a real name in
        # a row for VRBO, Northbeam, Pritchard, Amplitude and others because
        # the glued run was their sole occurrence in that answer. Truncating
        # keeps the real name and drops only the run-on garbage after it.
        if "\n" in span:
            span = span.split("\n", 1)[0].strip().strip(".,;:")
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
        # STOPLIST (WO-2026-09-06-A/-C): rubric labels, regulatory bodies,
        # and category acronyms mechanically pass the bars below without
        # being a business. Exact match only, never substring.
        if key in _STOPLIST:
            continue
        # Pronoun-contraction artifact ("I'll", "I'd", ...), see above.
        if _CONTRACTION_ARTIFACT.match(span):
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