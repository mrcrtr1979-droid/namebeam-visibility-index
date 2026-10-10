# Reproduce the Edition 1 headline numbers

This kit recomputes the headline numbers of Edition 1 from the data pack in `releases/edition-1/`. It compares them with the expected values kept beside the pack. It runs offline. It needs no installed packages and makes no network requests.

Run every command from the root of the repository.

## Commands

```
python3 -I scripts/reproduce_edition.py --selftest
python3 -I scripts/reproduce_edition.py --pack releases/edition-1
python3 -I scripts/reproduce_edition.py --pack releases/edition-1 --write-expected
```

- `--selftest` builds a small fixture pack in a temporary folder. Its WA raw persistence is 2 of 3, worked out by hand, and the selftest checks that the kit gets that figure. It writes the expected files, runs the check and expects every line to match. It then plants a wrong expected value and expects a mismatch, restores the file, changes one input file and expects that file's hash to be reported, and restores it. It prints `SELFTEST PASS` or `SELFTEST FAIL`. The fixture uses a stand-in gate module. The real gate code is not used by the selftest.
- The plain command runs the reproduction on the pack and prints one line per check.
- `--write-expected` writes `EXPECTED_HEADLINES.csv` and `EXPECTED_INPUTS.csv` into the pack. Run it when the pack is final. It writes nothing else.

## What the kit checks

Each check prints one line with these columns: MATCH or MISMATCH, name, window, numerator, denominator, value, and basis.

- **Headline checks.** Next-day persistence for Perplexity, raw and gated, in each window, and the KC page counts. The kit recomputes these from the answers and sources tables in the pack, using the formulas in `scripts/headline_recompute.py`. Each one is compared with `EXPECTED_HEADLINES.csv`.
- **Metrics checks.** Rows of `metrics/metrics_v2.csv` for the methods P.raw.pername, P.gated.pername and J.raw.pair_bothnamed.median, for every engine. The four Perplexity rows for P.raw.pername and P.gated.pername in WA and WB are compared with the recompute: the numerator, denominator and value printed in the file must equal what the kit recomputes. Every other row has no recompute in this kit. For those rows the check is the SHA-256 of `metrics/metrics_v2.csv`. The row reads MATCH when that hash equals the value in `EXPECTED_INPUTS.csv`. The numbers on those rows are printed as they appear in the file and are not recomputed here.
- **Input checks.** Every file the run read is hashed and compared with `EXPECTED_INPUTS.csv`. These are the pack's data tables, the gate code the pack ships and imports, the quarantine file that gate reads, and `scripts/headline_recompute.py`. The list is recorded during the run, so a file the numbers depend on is hashed even when the kit does not name it.

The windows are WA, 2026-09-01 to 2026-09-27, and WB, from 2026-09-28 to the end of the pack window in `PACK.json` (2026-10-07). For the KC rows the numerator is the number of answers and the denominator is the number of run days.

The basis column says how each line was checked. "recomputed" means the value was computed in this run. "sha256 of metrics_v2.csv" means the row is pinned by the file hash alone.

For the Edition 1 pack as committed, a full run prints 51 checks.

## What MATCH means

A line reads MATCH when the value this run produced equals the expected value, when a printed metrics value equals the recompute, or when a file's SHA-256 equals the expected hash.

The last line reads `REPRODUCE MATCH (n checks)` when every line matches. The exit code is 0. It reads `REPRODUCE MISMATCH` when at least one line does not match, with the count, and the exit code is 1. A run that cannot finish, for example because a file is missing, also ends with `REPRODUCE MISMATCH` and gives the reason.

## Requirements

Python 3.8 or later. The kit uses the standard library and nothing else, and it installs nothing. It writes files in the pack when `--write-expected` is given. The selftest writes into a temporary folder and nothing else. The kit writes no bytecode. It was run on Python 3.8.20, 3.11, 3.12 and 3.13.

## What this kit does not do

The gated figures run the gate code that ships in the pack. The kit checks that the arithmetic and the file hashes agree with the expected values. It does not re-derive the gate rules, and it does not rebuild the answers table from the raw corpus. That rebuild is a separate check in `releases/edition-1/verify.py --rebuild`.

## verify.py and this kit

`releases/edition-1/verify.py` checks every file of the pack against `MANIFEST.sha256`. With `--metrics` it also reruns the metrics code and compares the result with `metrics/metrics_v2.csv`. It checks file hashes.

This kit recomputes the numbers. It reads the pack's tables with the formulas in `scripts/headline_recompute.py`, and it hashes every file it read. The two checks answer different questions and can both be run.

## The two expected files and verify.py

`EXPECTED_HEADLINES.csv` and `EXPECTED_INPUTS.csv` are not listed in `MANIFEST.sha256`. While they sit in the pack, `verify.py` prints an EXTRA line for each one and ends with `VERIFY MISMATCH`. The rest of its output stays MATCH. Add the two files to the manifest when the pack is rebuilt, or remove them before running `verify.py`.
