# E1 Siri panel table

One row per Siri answer turn, from one iPhone (Siri AI beta, St. Louis area): four typed days (2026-10-03 to 2026-10-06) and one spoken day (2026-10-07). `siri_panel.csv` is built by `scripts/build_siri.py` from the filed panel notes.

## Columns

| column | meaning |
|---|---|
| date, mode, day_in_mode | run date as filed (local date), typed or spoken, and the day number within that mode |
| q_id, turn | question number in the panel protocol (Q1 to Q7) and the answer turn; turn 2 is Siri's reply after a follow-up, such as "It's on" after a location failure |
| city, scope | the city named in the question; scope is city, near me or nationwide |
| question_text, question_text_source | the question as asked, and whether it is the protocol wording or the text read from the screen |
| tag | S = Siri answered itself, C = handed to ChatGPT, W = web links alone or no help |
| siri_said, siri_said_source | what Siri said: the speech transcript for the spoken run (audio errors left as produced), the on-screen answer paragraph read by OCR for typed runs (OCR errors left as produced), empty when the filed note holds no text |
| businesses_named, n_businesses_named | businesses named, in the order given |
| sources_shown, source_names, sources_captured | the source chips shown with the answer as written on screen, the source names without counts, and whether any chip was captured |
| format, note | how the answer was laid out, and anything that qualifies the row |
| source_file, source_sha256 | the filed note the row was read from and its SHA-256 |

## Counts

As of 2026-10-07: 24 questions (S 20, C 0, W 4), 19 typed and 5 spoken, 28 answer turns. 21 of the 24 questions named at least one business. Namebeam is named in 0 of the 28 rows. The four W questions are all the near-me question. In three of them Siri named no business (it reported that it could not find the location on 2026-10-03 and 2026-10-04, and pointed to listing platforms on 2026-10-06); on 2026-10-05 its second reply, after a location failure, named four.

## Limits

- One phone, one place, five days. Small numbers; read them with that in mind.
- The spoken run reads the opening paragraph aloud; a list below it on screen was not read. Spoken names are the spoken ones.
- The typed pass of 2026-10-07 was not attached, so the same-day typed and spoken comparison is open.
- Day 1 (2026-10-03) screenshots are not transcribed in the filed note, so those rows carry no siri_said text.
- The filed notes stay private (they carry mailbox identifiers and internal remarks). The SHA-256 ties each row to the exact file.

## Rebuild and check

    python3 -I scripts/build_siri.py --src PATH_TO_BRAIN/corpus --out datasets/e1/siri
    python3 -I scripts/build_siri.py --selftest      # fixture test, including checks that must fail

The build stops with an error if a named business or a source name is not in the filed note, or if the S, C, W counts disagree with the note's own totals.

Operated by Carter Enterprise LLC. Questions about the record: namebeam.ai.
