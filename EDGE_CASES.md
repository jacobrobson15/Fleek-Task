# Edge Case Register — implementation map

Every case from the build plan's Section 10, mapped to the code that handles it.
★ = build-critical (would break the live demo). All ★ cases are covered by
`selftest.py`, which passes end-to-end.

## A. Cleaning — `cleaning.py`
| # | Case | Handling |
|---|---|---|
| A1 ★ | URL vs @ handle | `canon_handle()` strips `instagram.com/`, `@`, casing, trailing slash **before** any dedupe. |
| A2 ★ | Same store / different city | `dedupe_key()` for shops = `store + city + country` → "Second Threads" in two cities stays two leads. |
| A3 | £9,000 = cap | `clean_spend()` flags `spend_is_cap`; kept for ranking, ties break on velocity (`scoring.order_band`). |
| A4 | last_touch < first_seen | Flagged `last_touch_before_first_seen`; `engine._anchor_date` falls back to `first_seen`. |
| A5 | future first_seen | `first_seen_future` flag → `scoring.assign_band` returns no band until the date arrives. |
| A6 | blank / 0 velocity | `to_number()` → `None`; `scoring._normaliser` scores missing as the **median**, not zero. |
| A7 | junk email | `info@` passes; `test@test.com`-type flagged `email_junk`. |
| A8 | malformed phone | `clean_phone()` rejects too-short/long → `phone_unusable`, no call task. |
| A9 | phone as numeric | `clean_phone()` strips a trailing `.0` before formatting. |
| A10 | unfixable email | `ines@@` → `ines@` (obvious repair); uncertain → `email_unparseable`, never guessed. |

## B. Dedup / merge — `dedup.py`
| # | Case | Handling |
|---|---|---|
| B1 ★ | within-batch dupes | `dedupe_within()` collapses a batch against itself before merging. |
| B2 | disagreeing dupes | `merge_rows()` keeps most-advanced stage (`STAGE_RANK`), inbound from most-recent touch, loser preserved in notes. |
| B3 | dupes / different accounts | `merge_rows()` locks to the most-recent touch's account; the other is noted. |
| B4 ★ | @driftarchive | `_adds_information()` → a barren day-2 "new" duplicate of an engaged lead is **skipped**, never re-added as a DM; stage never downgraded. |
| B5 | reseller + shop overlap | A handle on a store row → `lead_type = shop` (clearer goal) + `handle_on_store` flag (`classify.py`). |

## C. Classification — `classify.py`
| # | Case | Handling |
|---|---|---|
| C1 | phone-only reseller | Callable but resellers aren't cold-called → `human_owner_status = manager_review`, flag `reseller_phone_only`. |
| C2 | no contact at all | `primary_channel = no_contact` → manual review, flag `needs_enrichment` (not a silent skip). |
| C3 | closed + new inbound | `logic.save_reply()` on a closed lead with `keep_talking` flags `lost_reactivation_review` → manager review, never auto-reactivates. |

## D. Prioritisation — `scoring.py`
Banding (`assign_band`), composite score (`compute_scores`), tie-breaks
(`order_band`). See README "Prioritisation logic".

## E. Per-track engine — `engine.py`
| # | Case | Handling |
|---|---|---|
| E1 ★ | one channel books | `apply_outcome()` on a booking → `end_all_tracks()` (every step maxed, all next-dates cleared, warm_call off). No contradictory message. |
| E2 | positive, no phone | `apply_outcome` arms `warm_call_due` only if a phone exists; otherwise the engaged channel's cadence carries the warm message. |
| E3 | reply on uncontacted | `conversation_channel` locks to where they engaged; a new lead with inbound lands in REPLY NEEDED. |
| E4 | cold tracks parallel | A new shop schedules Email 1 **and** Cold call 1 on day 0 (`_initial_state`); phone never waits for email. |

## F. Accounts — `accounts.py` (+ guards in `app.py`)
| # | Case | Handling |
|---|---|---|
| F1 | per-account cap | `apply_capacity()` fills `cap − sent_today` slots, follow-ups first. |
| F2 | follow-ups lock to account | `assign_dm_accounts()` never reassigns a lead that already has an account. |
| F3 | increase accounts mid-day | New ACTIVE account → capacity added immediately on next build. |
| F4 | decrease / lower cap | UI confirms; `remaining_slots()` clamps to ≥ 0; `overage()` surfaced honestly; in-flight never reassigned. |
| F5 | delete with live convos | `can_delete_account()` blocks → "reassign or archive N first". |
| F6 | paused with follow-ups due | `blocked_followups()` → banner, not dead tasks. |
| F7 | 41st DM sent anyway | `record_dm_sent()` always records; `overage()` shows "N over cap", reality not refused. |

## G. Time / date — `data_store.py`, `accounts.py`, `logic.py`
| # | Case | Handling |
|---|---|---|
| G1 ★ | run twice same day | `tracks_initialized` + the step-current guard in `mark_sent()` mean a 2nd open re-draws nothing and re-counts nothing. |
| G2 | not opened 3 days | `VISIBLE_CAP` per band → "X due — showing top N by score". |
| G3 | DEMO_TODAY rolls over | `roll_day_if_needed()` zeroes `sent_today`; leads sent under the old day stay sent. |
| G4 | weekend next_action | **Not built** (mentioned). Easy add: bump call `next_date` to the next weekday. |

## H. Reply flow — `engine.py`, `logic.py`, `app.py`
| # | Case | Handling |
|---|---|---|
| H1 | 2nd reply before acting | `apply_outcome()` keeps the latest in `last_inbound_text`, prior preserved in notes. |
| H2 | won on wrong lead | Terminal outcomes require a confirm checkbox; per-lead `undo_last()` reverses it (and refunds the DM slot). |
| H3 | reply, no outcome | The Reply form defaults to "keep talking"; an outcome is always recorded. |

## I. Upload / state robustness — `logic.py`, `data_store.py`
| # | Case | Handling |
|---|---|---|
| I1 ★ | upload fails halfway | `data_store.save()` writes to a temp file then `os.replace` — atomic, never half-written. |
| I2 ★ | double-tap / rerun | `mark_sent()` only advances if the step is still current → re-advancing today is a no-op. |
| I3 | wrong file uploaded | `upload_batch()` computes key overlap; UI warns "shares only X% of keys — sure?". |
| I4 | empty / header-only | "0 rows — nothing to add" = success. |
| I5 | missing optional cols | `clean_dataframe()` fills blanks; only missing `lead_id`/`stage` hard-blocks the upload. |

---

### Build-critical subset (verified by `selftest.py`)
`B1` within-batch dedup · `B4` @driftarchive skip · `G1` run-twice idempotency ·
`I2` idempotent Sent · `I1` atomic upload · `A1` handle canonicalise ·
`A2` store+city key — **all passing.**
