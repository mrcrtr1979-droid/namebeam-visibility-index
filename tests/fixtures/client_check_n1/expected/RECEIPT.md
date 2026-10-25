# Namebeam Client Check receipt, 2026-10-27

- Run folder: client_checks/2026-10-27
- Input: in.json (SHA-256 465ddf01b1a58dad85338ed8dfa3155806beac13848bbbef2a860cf1e3e2e7d2)
- Engines: perplexity, gemini, openai, anthropic
- Items: 2 | Requests: 8 | OK: 8 | FAIL: 0
- Method: each question sent verbatim to each engine, one dated run, API channel (no memory, no personalization; results can differ from logged-in consumer apps). SHA-256 is of the answer text as saved (UTF-8). Full answer text is in the per-answer JSON file named below, not repeated here.
- Client Appears in Answer: exact = the client string, case-insensitive, in the answer. loose = punctuation-insensitive match or every distinctive word on one line; a human confirms it. first_three_named: the first three businesses the answer names, ordered by first appearance. Built from the record's name extractor (scripts/e1_runner.py extract_competitors) plus numbered headings, then filtered: place names, headings and generic phrases, sentence fragments, directories and utilities, and equipment manufacturers listed as brands (Carrier, Trane, Lennox and similar) are dropped, and so is a name that sits inside a longer name in the same list. The client, or an intake alias, is listed in its position when the answer names it. Mechanical, not a human read: an unusual business name can be missed and a look-alike can slip through. competitors_mentioned is the record's own field (client excluded) and is not altered by this rule.
- Request Cost: tokens as returned by the engine (in / out / total). No price is applied here.

## 1. T-01-alpha

- **Question:** best roofing company in Springfield MO
- **Target Client:** Acme Roofing (Springfield, MO, roofing)

| Engine | Run Status | Client Appears (exact / loose) | First Three Businesses Named | SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | Rail | File |
|---|---|---|---|---|---|---|---|---|
| perplexity | OK | yes / yes | Acme Roofing; Zed Roofing; Orbit Roofing | `843c5b4c533f05f99e75fffb53f7b467cb20c37ff7ab0dd33873d719f73bf800` | 7 / 11 / 18 | perplexity-model-x | n/a | T-01-alpha_perplexity.json |
| gemini | OK | yes / yes | Acme Roofing; Zed Roofing; Orbit Roofing | `bde58137fb8a13fea3d48940abb43cbb682cd7750aebdc382d3c11e6b9280aac` | 7 / 11 / 18 | gemini-model-x | n/a | T-01-alpha_gemini.json |
| openai | OK | yes / yes | Acme Roofing; Zed Roofing; Orbit Roofing | `148686e118ff9b871dd2caa593de118e02a78d337e27b11e5201ca9e7242db74` | 7 / 11 / 18 | openai-model-x | n/a | T-01-alpha_openai.json |
| anthropic | OK | yes / yes | Acme Roofing; Zed Roofing; Orbit Roofing | `466874f45be1bf099ec06abb11129feb802689209dfdf0aa491a64b83ff71cb8` | 7 / 11 / 18 | anthropic-model-x | n/a | T-01-alpha_anthropic.json |

## 2. T-02-beta

- **Question:** best law firm in Oklahoma City
- **Target Client:** Doerner, Saunders, Daniel & Anderson (Oklahoma City, OK, law firm)

| Engine | Run Status | Client Appears (exact / loose) | First Three Businesses Named | SHA-256 of Answer Text | Request Cost (tokens in/out/total) | Served Model | Rail | File |
|---|---|---|---|---|---|---|---|---|
| perplexity | OK | no / yes | Crowe & Dunlevy; Doerner Saunders Daniel & Anderson; McAfee & Taft | `b7a4a67aa157b644a92745d586e90ba3de658206fc3f5a080d59aafa7374b77a` | 7 / 11 / 18 | perplexity-model-x | n/a | T-02-beta_perplexity.json |
| gemini | OK | no / yes | Crowe & Dunlevy; Doerner Saunders Daniel & Anderson; McAfee & Taft | `a8e954bcb702453d295b204c866482aaa2bc04c28b86d45c14a0b43d0fea3db4` | 7 / 11 / 18 | gemini-model-x | n/a | T-02-beta_gemini.json |
| openai | OK | no / yes | Crowe & Dunlevy; Doerner Saunders Daniel & Anderson; McAfee & Taft | `a823774513512acb87f9b8834b92d9e88dcf7c7e6c77878b839c0f6a2eccb03f` | 7 / 11 / 18 | openai-model-x | n/a | T-02-beta_openai.json |
| anthropic | OK | no / yes | Crowe & Dunlevy; Doerner Saunders Daniel & Anderson; McAfee & Taft | `68780467848f22da2a66f3e37f1e243ee42af1f6f6e91437821320cbd120ef74` | 7 / 11 / 18 | anthropic-model-x | n/a | T-02-beta_anthropic.json |

