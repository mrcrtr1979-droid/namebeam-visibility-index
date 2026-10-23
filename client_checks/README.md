# Namebeam Client Check runs

This folder holds Namebeam Client Check runs: one client business, one buyer question, four engines (Perplexity, Gemini, OpenAI, Anthropic), one dated run, a SHA-256 of every answer text. Each run is a one-off, never a schedule.
A run writes client_checks/<run date>/: one JSON per question per engine, a RECEIPT.md and a SUMMARY.json. A same-day second run goes to <run date>_r2, never over the first. Inputs live in client_checks/inputs/ (first one: AGENCY_1027.json, 17 agency-client questions).
To run one: GitHub Actions, workflow "Client Check", Run workflow; set input_path, engines and dry_run. dry_run defaults to true and calls no engine; untick it for a live run, which commits client_checks/** only.
Questions go to the engines verbatim over the API, no memory or personalization. A failed call is saved as run_status FAIL with its error class; no answer is ever filled in.
Rows use the daily record's field set and name extractors, plus answer_sha256, client_named (exact, case-insensitive), client_named_loose and first_three_named. These are mechanical; a person confirms them before they are quoted.
Gemini runs on the free-tier rest model (gemini-3.5-flash-lite) because a client check is never on the continuity panel; the served model is in engine_meta and the RECEIPT.
This folder is separate from the daily record: the Client Check never reads or writes corpus/e1, and its rows are not counted in Edition 1 or any record headline.
Licence, same as the record (see NOTICE.md): CC BY 4.0 covers our compilation, code, annotations and derived columns. It does not cover the raw answer text returned by each AI service, published here as a dated factual record of what each service returned.
Credit line: Namebeam AI Visibility Record (Carter Enterprise LLC), CC BY 4.0.
Operated by Carter Enterprise LLC.
