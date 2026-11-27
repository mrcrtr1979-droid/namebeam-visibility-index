# CALIBRATION 1127 | what the tripwire's fires are worth, measured | CLOUD-DIVE-1127B (Claude Code on the laptop, Sonnet 5.5) | written 2026-11-27 4:12AM CT
PRECEDENCE (R108): LAW.md is the one law; Terry's newest words first. This is a return and a proposal. Rerun every number with the commands in section 10. Raw outputs sit beside this file (CALIBRATION_*_1127.txt).
WHY THIS FILE EXISTS: Terry, 2026-11-27 3:39AM CT: "make sure its not surface level nor the bricks, be confident in highest mathmeatics and probabilty". The tripwire fires at |z| >= 3, which is a 1-in-370 event for independent answers. The record holds 1,162 sites tested on 82 days, and the same prompts are asked every day, so the answers are not independent. The false-fire rate had to be measured, not assumed.

## 1. THE VERDICT IN SEVEN LINES
1. In the regime the instrument reads today (Perplexity agent-fast mode, a full 28-day baseline, 29 testable days) it has fired on 30 changes where pure noise gives 4.9 (1 to 10). At |z| of 4 or more: 18 fires, 6 percent noise. At 5 or more: 9 fires, 3 percent.
2. The newest-day list (2026-11-26, 8 fires) is about 95 percent real: placebo records fire on 0.39 sites that day. Three of the eight survive a test over all 1,162 sites at once at 0.05 (Expertise.com, OnToplist, Yahoo: any-site p of 0.0005, 0.004, 0.041) and a fourth at 0.10 (5 Star Home Service Pros, 0.061).
3. These fires LAST. Fourteen days later the 19 clean-regime fires with follow-up are still 7.8 points off their baseline; noise-selected sites are 2.0 points off (95 percent range -2.2 to +7.3). rho 0.88 against 0.22.
4. The 75 first-fire events of the whole backtest are NOT all real: noise explains 12 to 29 of them (16 to 39 percent), depending on how the placebo is built (section 4). The early record is the weak part: 45 of the 75 fired on a baseline of fewer than 24 data days.
5. The detector is miscalibrated for heavily cited sites: above 12 percent of answers it fires falsely 1.2 to 1.4 percent of days, ten times the nominal 0.13 percent, because answers cluster. Rare sites are tested conservatively.
6. The Reddit fall on Perplexity is real and now tested properly: best split after 2026-09-13, minus 30.0 points (23.4 to 36.4), scan-corrected p under 0.0001.
7. The Gemini engine shows 3 fires against 0.3 expected, all three clear a test over every site; two of them (Angi up, Attorney at Law Magazine down) also fired on Perplexity in the same direction, a 2.2 percent coincidence.

## 2. METHOD (tools/source_share_null.py; standard library plus numpy; zero model calls)
- REPLICA, PROVEN. A vectorised copy of check() reproduces every first-fire event of the tool, with the same day, direction, counts and z (largest difference 0.0): 75 of 75 on Perplexity, 3 of 3 on Gemini (`--equal`), and a seeded synthetic record (`--selftest`).
- PLACEBO RECORDS. The unit of exchange is the DAY: all answers of a day move together, so dependence inside a day is kept. Days are shuffled only among days of the same collector mode (and, in the strict version, the same count of answers per question set). A shuffled record has no real change in time by construction. B = 2,004 records for Perplexity, 1,008 for Gemini. The detector is run on each exactly as on the real record.
- FALSE-DISCOVERY RATE of a list at cut c: N_null(c) / N_real(c), where N_null is the placebo mean count of (site, direction) pairs whose peak |z| reached c (Efron's empirical FDR with a permutation null). Family-wise p of one fire: share of placebo records whose single largest |z| (any site, any day) reached it (Westfall and Young max-T). q of an event: the FDR of the list cut at its own peak.
- KNOWN-TRUTH TEST of the estimator itself: 400 placebo records, each with 16 planted sustained changes (6, 10, 15, 25 points; 70 percent up). The false-discovery rate that really happened against the rate the placebo predicted: cut 3: 31.0 against 31.8 percent; cut 4: 12.0 against 11.1; cut 5: 4.2 against 3.5. The estimate is within one point of the truth and slightly optimistic at the high cuts.
- RECALL from the same test: a planted change is found within 14 days in 70 percent of cases at 6 points (median 5 days to first fire), 96 percent at 10 (4 days), 100 percent at 15 (2 days) and 25 (1 day).

## 3. THE THREE READINGS OF THE DETECTOR (Perplexity, strict placebo; FDR = noise share of the list)
| reading | testable days | real events | placebo events (95 percent range) | FDR at 3 / 4 / 5 / 6 |
|---|---|---|---|---|
| as built (tests from the first day a 60-answer baseline exists) | 82 | 75 | 29.3 (22 to 40) | 39 / 26 / 23 / 21 percent |
| full baseline (24 or more of 28 days) | 35 | 47 | 15.3 (8 to 22) | 32 / 20 / 12 / 7 percent |
| CLEAN: last collector mode only, full baseline | 29 | 30 | 4.9 (1 to 10) | 16 / 6 / 3 / 2 percent |
Per event (as built, strict): q of 0.05 or less for 3 of 75, 0.10 or less for 7, 0.25 or less for 34. Clean: q of 0.05 or less for 18 of 30, 0.10 or less for 25 of 30, 0.25 or less for 30 of 30; 3 of 30 pass the any-site test at 0.05.
Distinct changes (sites that fire together on identical counts are one change): 66 for the 75 events; Eight sites fell together on 2026-09-18 on identical counts (0 of 145 against 27 of 430), one change in the engine's answers, not eight.

## 4. WHY TWO PLACEBOS AND WHAT THEY SAY
- STRICT (same mode and same daily design): in the default-mode era the design changed (15 question sets, then 21) at the same time as the engine did, so shuffling inside a design keeps the real change in the placebo. Its noise count for the early record is therefore an UPPER bound: 29.3 of the 75 (39 percent).
- LOOSE (same mode only): mixes days across designs, which removes the confound but lets composition shifts in; its noise count is 12.0 of 75 (16 percent), a LOWER bound on the early record. Per event it clears q of 0.05 for 25 of 75.
- The truth for the early record lies between 16 and 39 percent noise. In the CLEAN regime the design is identical on all 59 days, both placebos agree (4.9 of 30) and the question does not arise. Every claim above that is made for the live regime uses the clean numbers.
- Persistence under the loose placebo for the whole record: rho real 0.96 against 0.37 (0.04 to 0.76). Under the strict placebo it reads 0.96 against 0.86, which is the contamination, not a finding.

## 5. PERSISTENCE (does a fire still hold 14 days later)
rho = sum(follow minus baseline) / sum(window minus baseline), down moves sign-flipped. Pure noise with a long baseline gives about 0.2 (checked on independent answers: 0.22 to 0.28); a shift that stays gives 1 or more, because on the day it first fires the window holds only part of it (planted +30 points: rho 2.5).
| set | events with follow-up | rho real | rho placebo (95 percent range) | next-14-day shift, points: real, placebo |
|---|---|---|---|---|
| clean regime, all first fires | 19 | 0.88 (0.57 to 1.17) | 0.22 (-0.25 to 0.71) | +7.8, +2.0 (-2.2 to +7.3) |
| clean, z 3 to 4 at first fire | 13 | 1.02 (0.70 to 1.31) | 0.21 | +7.8, +1.9 |
| clean, z 5 or more at first fire | 3 | 1.07 (0.17 to 2.00) | 0.20 | +12.4, +2.8 |
89 percent of the clean fires are still on the same side of the baseline after 14 days. Longer horizons are UNMEASURED.

## 6. DESIGN EFFECT AND POWER (placebo noise, the live regime, window of 105 answers and baseline of 588 on 2026-11-26)
- Variance of z on a placebo record (1.0 means the normal approximation is exact), for the 17 sites cited 150 or more times: median 1.22 (strict) or 1.29 (loose); 10th to 90th percentile 0.55 to 2.3; 53 percent of them above 1.2.
| pooled share | sites | step found 8 times in 10 (placebo noise) | same if answers were independent | false fires on a day with no change |
|---|---|---|---|---|
| 1 to 3 percent | 134 | 10 points | 6.5 | 0.02 percent |
| 3 to 6 percent | 58 | 8 | 9.5 | 0.11 to 0.14 |
| 6 to 12 percent | 11 | 12 | 12.5 | 0.39 to 0.44 |
| 12 to 25 percent | 8 | 16 | 15.0 | 1.2 to 1.4 (nominal 0.13) |
- A recommended repair, not built here: divide each site's z by the square root of its placebo variance (studentise), refreshed weekly. It would hold the false-fire rate of heavily cited sites at the nominal rate and sharpen rare ones. It has to be cross-fitted (variance from one half of the placebo records, tested on the other) to be believed.

## 7. THE LIVE LISTS WITH THEIR ODDS (as of 2026-11-26 Perplexity; 2026-11-19 Gemini)
| site | way | z | cites on file | P(noise) this site | P(noise) any of 1,162 sites |
|---|---|---|---|---|---|
| expertise.com | up | +8.1 | 716 | 0.0005 | 0.0005 |
| ontoplist.com | up | +6.3 | 230 | 0.0005 | 0.0040 |
| yahoo.com | up | +4.5 | 221 | 0.0005 | 0.041 |
| 5starhomeservicepros.com | up | +4.2 | 51 | 0.0030 | 0.061 |
| consumeraffairs.com | down | -3.8 | 376 | 0.0005 | 0.098 |
| homematchup.com | down | -3.4 | 107 | 0.0040 | 0.162 |
| google.com | up | +3.4 | 77 | 0.0005 | 0.166 |
| forbes.com | up | +3.3 | 295 | 0.0209 | 0.182 |
| Gemini: attorneyatlawmagazine.com | down | -4.0 | 57 | 0.0010 | 0.0040 |
| Gemini: angi.com | up | +3.7 | 29 | 0.0040 | 0.0050 |
What google.com is: its 11 citations in the last 7 days are Google Sites and Google Docs pages (sites.google.com/view/health-insurance-kansas-city/home five times, sites.google.com/view/places-to-buy-running-shoes four times), not Google's own properties. The domain rule (keep the last two labels of a host) affects 2 of 29,105 Perplexity citations (0.01 percent) and none on a list.
Cross-engine replication: 66 sites are testable on both engines. Two of the three Gemini fires land on those, and both are Perplexity fires in the same direction (Angi up, Attorney at Law Magazine down); the chance that both land on a Perplexity fire by luck is 0.022 (hypergeometric, 20 of 132 site-direction pairs).

## 8. THE REDDIT FALL, TESTED AT THE DAY
Perplexity, default mode, the 15-set panel, 2026-08-24 to 2026-09-26 (30 days, 490 answers): best single changepoint after 2026-09-13; before 160 of 299 (53.5 percent), after 45 of 191 (23.6 percent); difference minus 30.0 points, day-block bootstrap 95 percent interval minus 36.4 to minus 23.4; likelihood-ratio 44.6; scan-corrected permutation p over every split and 20,000 shuffles: 0.00005 (none of the shuffles reached it). The likelihood interval for the date is one day under a binomial model that treats answers as independent, so read it as plus or minus a day or two.

## 9. WEEKLY SHARES (what a table cell is worth)
A weekly share built from about 105 answers carries a 95 percent interval of about plus or minus 2.9 points at 2 to 10 percent, 5.5 at 10 to 25 percent and 9.5 at 25 to 60 percent (day-block bootstrap over the 7 days of a week, agent-fast weeks, 28 cells; the independent-answers formula gives 4.4, 7.1 and 9.3).

## 10. WHAT IS UNMEASURED, AND THE MEASUREMENT
- Persistence beyond 14 days: the record has to grow; rerun section 5 monthly.
- Whether Perplexity's default mode today still cites Reddit as it did in August: the 7-day two-mode test (THE_INSTRUMENT_1127.md section 6, move 1).
- Weekday cycles: placebo shuffles them away and the 7-day window cancels them except with missing days (2026-11-23 and 11-25 are missing). Measure by a placebo that shuffles within weekday.
- The Gemini model version is not recorded in the file. The collector's run log settles it.
- The studentised detector (section 6): cross-fit it on the placebo ensemble, then compare false fires and recall at equal list length.
- RERUN: `python3 -I tools/source_share_null.py --selftest`; `--engine perplexity --equal`; `--engine perplexity --perms 2000 --jobs 12` (strict; add `--strata mode` for loose); `--engine gemini --perms 1000`; `--validate --sims 400 --perms 800`; `--reddit`. About 3 minutes each on 12 cores; numpy required.
