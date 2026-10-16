# Edition release checklist (from Edition 2 on)

Owner of each step is printed; nothing public moves without the Terry gate at the end. Every step leaves a receipt (file or commit hash). ASCII only in every public file; the copy-law scanner must be GREEN on every page or card line before it goes live.

| # | Step | Command or control | Receipt | Owner |
|---|---|---|---|---|
| 1 | Freeze the window. Last run day in, first run day of the next edition named. | decide [end]; the collector keeps running into the next window | bus line | COS |
| 2 | Build the pack from the corpus. | python3 -I -X utf8 scripts/build_edition.py --edition [N] --start [start] --end [end] (as Edition 1 was built, FINAL mode) | releases/edition-[N]/ files, PACK.json | credit seat |
| 3 | Add run_status to the answers table (built in from Edition 2; the Edition 1 file was added after release). | the run_status step of build_edition (or reproduce/run_status/add_run_status.py) | answers_*_run_status.csv or the column in place | credit seat |
| 4 | Verify: pack, rebuild from corpus, recompute metrics. | python3 -I -X utf8 releases/edition-[N]/verify.py --rebuild --metrics | log with VERIFY MATCH and the file count | credit seat, COS re-runs |
| 5 | Second lane on every headline number (stability, agreement, name rates) with its own code. | reproduce/stability/niche_findings.py plus an independent script (Edition 1: three code paths agreed) | status/returns receipt with both numbers | credit seat (Haiku lane) |
| 6 | README from releases/TEMPLATE/README_TEMPLATE.md, every [FILL] replaced, seven sections with anchors kept. | copy-law scanner GREEN; banned words zero; ASCII zero | README.md in the pack | credit seat |
| 7 | Manifest and timestamp. | MANIFEST.sha256 regenerated; ots stamp MANIFEST.sha256; wait for a Bitcoin block; ots upgrade; record the block | MANIFEST.sha256.ots, block number in README | credit seat (stamp), COS (block read) |
| 8 | CORRECTIONS.md: add a row for anything learned about earlier editions during the build; never edit released rows. | one row per correction with date, file, what, how found | CORRECTIONS.md | credit seat |
| 9 | Branch and PR. | branch edition-[N]-release, PR titled "Edition [N]: [window], [rows] rows" | PR link | credit seat opens, COS verifies, Terry merges |
| 10 | Hugging Face card and dataset files. | hf_publish_edition workflow after merge; card counts line, file table, licence line from the README | run id, card URL | COS |
| 11 | Pages: markets.namebeam.ai/edition-[N]/ (counts line, findings, licence sentence, JSON-LD with datePublished), home page cadence line and "data through" date, Edition 1 page gains a "newer edition" link. | namebeam-markets and namebeam-site PRs | PR links | 1013D (pages), Terry merge |
| 12 | Listings: Datarade and any marketplace copy updated to the new counts; same price everywhere. | LISTINGS_FIX-style diff file | diff file, Terry's paste | credit seat drafts, Terry pastes |
| 13 | Announcement set (press note, warm-thread notes, reviewer link). One pitch, one follow-up rule; precheck DO_NOT_RESEND and OUTREACH_LOG. | drafts in status/staged, sends on Terry's batched yes | OUTREACH_LOG rows | COS sends after Terry's yes |
| 14 | Registry: enroll the new edition in the daily machine registry with its cadence; retire nothing silently. | daily_machine_registry.csv row | row id | credit seat |
Gate: steps 9 (merge), 11 (merge), 12 (paste) and 13 (send) are Terry's; every other step is a seat's. If step 4 is not VERIFY MATCH, nothing after it runs.
