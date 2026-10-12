# Changelog

- 2026-10-13: scripts/e1_runner.py extract_competitors returns the de-duplicated union of bold-span and plain-text names, headings dropped, then a gazetteer pass adding names recorded earlier for the same market x niche by any engine when they appear in the text (DQ-5). Effective for collection runs dated 2026-10-14 and later. Edition 1 (through 2026-10-13) is unchanged. Replay: scripts/replay_extractor_union.py.
