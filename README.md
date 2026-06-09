# Fleek Pipeline Tool

The brain that runs the GTM-Acquisition team's daily outreach. It inherits a
messy pipeline of ~265 leads, cleans it, decides **who to contact next and why**,
drafts every message, schedules follow-ups across parallel tracks, and keeps
running day after day without repeating itself or letting warm leads go cold.

It is **not** a CRM, dashboard, or sending tool. It decides the next action; the
rep (or an agent) executes it: *read it, copy it, send it, hit Sent. If they
reply, hit Reply and say what they said.* ~30 seconds to train.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Optional — LLM drafting (OpenAI `gpt-4o-mini`). Without a key the tool falls back
to built-in templates, so the demo always works:

```bash
export OPENAI_API_KEY=sk-...      # or add it to .streamlit/secrets.toml
```

Verify the brain end-to-end (the four traps + idempotency):

```bash
python selftest.py
```

### The demo flow
1. Open the app — the **progress board** and the **four-band activity list** load.
2. Work a card: read the **Why**, copy the drafted message, send it for real,
   hit **Sent**. Hit **Reply** when a prospect responds.
3. In the sidebar, **upload `data/new_drop_day2.csv`** — see the merge diff.
   Upload it **again** — nothing changes (idempotent).
4. **Reset** winds everything back to the frozen clean copy for a repeat run.

---

## Architecture

```
data/pipeline.csv (database)  ──┐
data/new_drop_day2.csv (batch) ─┤──→  logic.py  ──→  app.py (Streamlit)  ──→  Streamlit Cloud
                                │      (brain)        (list-view UI)
pipeline_clean_original.csv ────┘
        (reset anchor)
```

Every read/write goes through one **data layer** (`data_store.py`), so the CSV
backing can later be swapped for Postgres/SQLite with **no change to the brain**.
Writes are atomic (stage → validate → `os.replace`) — never a half-written file.
The logic is built to still work at 30,000 leads (vectorised cleaning, keyed
dedupe, per-account capacity).

| Module | Responsibility |
|---|---|
| `config.py` | Hardcoded constants (Section 11). Only `DM_DAILY_LIMIT` is user-exposed. |
| `cleaning.py` | A1–A10 — handles, dates, spend, phones, emails, stages. |
| `dedup.py` | B1–B5 — `dedupe_key`, within-batch + against-pipeline merge. |
| `classify.py` | C1–C3 — lead_type, channel, goal, track eligibility. |
| `engine.py` | E1–E4 — the per-track sequence engine (the heart). |
| `scoring.py` | Banding + composite score + the Why line. |
| `accounts.py` | The IG-account capacity layer + guards (F1–F7). |
| `drafting.py` | LLM drafting, hash-gated so it never repeats itself. |
| `data_store.py` | The single data layer (atomic CSV, reset, demo clock). |
| `logic.py` | Facade the UI calls: `build_state`, `mark_sent`, `save_reply`, `upload_batch`, … |
| `app.py` | Thin Streamlit list-view. Calls `logic.py` only. |

See [`EDGE_CASES.md`](EDGE_CASES.md) for every edge case mapped to the code that
handles it.

### Lead type vs. contact channel (the central distinction)
Lead type and contact channel are kept strictly separate. A Depop **reseller**
with an email is still a reseller chasing a **call** — just contacted by email. A
**shop** reachable only by handle is still a shop chasing a **visit** — first
touched by DM. The two are never conflated.

### Multi-threaded sequences
A lead can run **DM + Email + Cold-call tracks in parallel**. The list surfaces
whatever is due next on *any* track; the rep never sees "track" or "step number".
A booking outcome on **any** channel ends **every** track immediately, so no
contradictory message can ever go out after a win.

### Idempotency (provable)
- **Sent** only advances a step if that step is still current — a Streamlit
  double-rerun or a second tap is a no-op.
- **Drafts** are keyed by a content hash; identical state reuses the stored draft
  and never re-calls the model or re-sends — `last_message_hash` makes "doesn't
  repeat itself" provable.
- **Upload** the same file twice → within-batch + against-pipeline dedupe collapse
  it to zero changes.

### What's CSV-for-now (debrief)
CSV persistence is fine for the demo. In production the *same* logic moves behind
Postgres/Supabase, because all reads/writes already go through one data layer.
Cut for now (mentioned, not built): automated reply sync (IG/Gmail APIs),
configurable day-gaps, weekend bumping, health charts.

---

## Prioritisation logic

Two independent ranking problems: which **band** a lead is in, and the **order**
within the band.

### Step 1 — banding (processed top-down)
```
1. REPLY NEEDED   inbound text present, lead not closed/owned
2. FOLLOW-UPS DUE a track has a step due today, conversation already started
3. NEW OUTREACH   never contacted, a track starts today
4. MANUAL REVIEW  data_quality_flags set, can't be actioned as-is
   (CLOSED/OWNED   won/lost/call-booked/visit-booked/negotiating — EXCLUDED from tasks)
```
Reply-needed always beats follow-ups, which always beat new outreach. This is the
brief's "don't let warm leads go cold" made literal.

> **Implementation note.** The data stores inbound text on many *mid-sequence*
> leads as context, so a literal "any inbound" reply-needed band would swamp the
> queue. We scope REPLY NEEDED to leads where the ball is genuinely in our court —
> stage `replied`/`warm`, or a brand-new lead that messaged us first. Mid-sequence
> `contacted`/`ghosted` leads keep flowing through follow-ups, with their last
> message surfaced in the Why line. The honest count is shown on the board; large
> bands render "showing top N by score".

### Step 2 — order within a band (single composite score, stated openly)
```
score =  0.30 · normalised(sales_velocity_30d)   ← strongest buying signal
       + 0.25 · normalised(est_monthly_spend_gbp) ← commercial value
       + 0.20 · stage_weight                       ← how warm
       + 0.15 · overdue_days                        ← urgency / don't go cold
       + 0.05 · normalised(followers)               ← reach (weakest)
       + intent_bonus                               ← inbound asked for call/bundle/pricing

stage_weight:  negotiating 1.0 · warm 0.8 · replied 0.7 · contacted 0.4
               · new 0.2 · ghosted 0.3
intent_bonus:  last_inbound_text contains call / bundle / pricing / "keen" → big flat add
tie-break:     velocity, then spend, then overdue-days
```

Missing metrics score as the **median** (never zero). A spend of exactly £9,000 is
a data cap meaning "unknown-high" — kept for ranking, with ties broken on velocity.

### Step 3 — capacity gate (DMs only)
```
For each active IG account: fill remaining slots (cap − sent today) with the
highest-scored DM leads assigned to it, follow-ups first.
Overflow rolls to tomorrow — never reassigned to another account (would change
the sender mid-conversation). Email and phone have no cap.
```

### Debrief one-liner
> "Velocity and spend decide commercial value; stage and overdue-days decide
> urgency; inbound intent overrides everything. Warm always beats big-but-cold.
> The 40-cap is applied per account after ranking, so we never sacrifice a hot
> follow-up for a cold high-follower account."

This directly answers the triage question: **"Day one, 40 DMs — who gets them and
why?"**

---

## Config (hardcoded — Section 11)

```python
DEMO_TODAY          = max(parsed source dates) + 1 day   # ≈ 2026-03-01
DM_DAILY_LIMIT      = 40          # PER active account (the one exposed setting)
DM_FOLLOWUP_DAYS    = [3, 4, 5]   # gaps to DM 2, 3, 4
EMAIL_FOLLOWUP_DAYS = [3, 4, 5]   # gaps to Email 2, 3, 4
COLD_CALL_GAP_DAYS  = 2           # cold call 2 after cold call 1
BOOKING_LINK        = "cal.ly/fleek"
SCORE_WEIGHTS       = {velocity:0.30, spend:0.25, stage:0.20, overdue:0.15, followers:0.05}
```

`DEMO_TODAY` is fixed to `max(parsed dates) + 1 day` so the demo isn't "all
overdue".

---

## Deploy to Streamlit Cloud
1. Push this repo to GitHub.
2. New app → point at `app.py`.
3. Add `OPENAI_API_KEY` under **Secrets** (optional — templates work without it).

The frozen clean anchor (`data/pipeline_clean_original.csv`) and the two source
CSVs are committed; the live working copy is rebuilt on first run.
