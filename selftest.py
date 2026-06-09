"""
selftest.py — verifies the four traps this dataset plants (build plan §14
'definition of done'), end-to-end through the logic facade, before any UI.

Run:  python selftest.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

import accounts
import config
import data_store
import engine
import logic
import scoring


def _fresh():
    """Start from a clean slate (delete live + accounts state)."""
    for p in (config.LIVE_PIPELINE, config.CLEAN_ORIGINAL, config.ACCOUNTS_STATE,
              config.STAGING_PIPELINE):
        if os.path.exists(p):
            os.remove(p)
    data_store._DEMO_TODAY = None
    data_store.ensure_initialized()


def _day2():
    return pd.read_csv(config.SOURCE_DAY2, dtype=str)


PASS, FAIL = "✅ PASS", "❌ FAIL"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{PASS if ok else FAIL}  {name}" + (f"  — {detail}" if detail else ""))


def main():
    print(f"DEMO_TODAY = {logic.get_demo_today()}\n")
    _fresh()

    # ---- Trap 1: upload day-2 twice → second run changes nothing -------------
    r1 = logic.upload_batch(_day2())
    after_first = len(data_store.load())
    r2 = logic.upload_batch(_day2())
    after_second = len(data_store.load())
    check("Trap 1a — first day-2 upload adds new leads", r1["added"] > 0, r1["headline"])
    check("Trap 1b — second identical upload is idempotent",
          r2["added"] == 0 and after_first == after_second,
          f"2nd: {r2['headline']} | rows {after_first}→{after_second}")

    # ---- Trap 2: @driftarchive never gets a fresh DM ------------------------
    df = data_store.load()
    da = df[df["handle_clean"] == "driftarchive"]
    one_row = len(da) == 1
    still_replied = one_row and da.iloc[0]["stage"] == "replied"
    check("Trap 2a — @driftarchive is a single lead (not re-added)", one_row, f"{len(da)} row(s)")
    check("Trap 2b — @driftarchive stayed 'replied' (not reset to new)", still_replied,
          da.iloc[0]["stage"] if one_row else "n/a")
    skipped_driftarchive = "L0294" in r1.get("skipped_ids", [])
    check("Trap 2c — day-2 L0294 was SKIPPED, not added", skipped_driftarchive)

    # ---- Trap 3: 'Second Threads' in different cities stays separate ---------
    st = df[df["store_clean"].str.lower() == "second threads"]
    cities = sorted(set(c for c in st["city_clean"] if str(c).strip()))
    check("Trap 3 — Second Threads stays split by city",
          len(st) == 2 and "London" in cities and "Amsterdam" in cities,
          f"{len(st)} leads, cities={cities}")

    # ---- Trap 4: a booking on one channel kills all parallel tracks ---------
    # Pick a lead with a live multi-track setup, book a visit, confirm silence.
    demo_today = logic.get_demo_today()
    df = scoring.compute_scores(data_store.load(), demo_today)
    target = None
    for _, r in df[df["band"] == scoring.BAND_NEW].iterrows():
        if r.get("email_active") and r.get("call_active"):
            target = r["lead_id"]
            break
    if target is None:
        target = df[df["band"] == scoring.BAND_FOLLOWUP].iloc[0]["lead_id"]
    pre = data_store.load()
    pre_lead = pre[pre["lead_id"] == target].iloc[0]
    pre_actions = engine.due_actions(pre_lead, demo_today)
    logic.save_reply(target, "Yes — come by Thursday", config.OUTCOME_VISIT_BOOKED)
    post = data_store.load()
    post_lead = post[post["lead_id"] == target].iloc[0]
    post_actions = engine.due_actions(post_lead, demo_today)
    band_after = scoring.assign_band(post_lead, demo_today)
    check("Trap 4 — booking closes every track (no contradictory follow-up)",
          len(pre_actions) >= 1 and len(post_actions) == 0
          and post_lead["stage"] == "visit_booked" and band_after == scoring.BAND_CLOSED,
          f"{target}: {len(pre_actions)} due → {len(post_actions)} due, stage={post_lead['stage']}")

    # ---- Bonus: idempotent Sent (I2) ----------------------------------------
    df = scoring.compute_scores(data_store.load(), demo_today)
    nb = df[df["band"] == scoring.BAND_NEW]
    if len(nb):
        lid = nb.iloc[0]["lead_id"]
        # find its first action key
        from logic import _assemble_actions
        acts = _assemble_actions(nb.iloc[0], demo_today)
        if acts:
            key = acts[0]["key"]
            logic.mark_sent(lid, key)
            t1 = int(data_store.load().set_index("lead_id").loc[lid, "num_touches"])
            logic.mark_sent(lid, key)  # double-fire
            t2 = int(data_store.load().set_index("lead_id").loc[lid, "num_touches"])
            check("Bonus — Sent is idempotent (double-tap = no-op)", t1 == t2,
                  f"touches {t1} == {t2}")

    # ---- Reset restores the anchor ------------------------------------------
    logic.reset_pipeline()
    reset_rows = len(data_store.load())
    check("Bonus — reset winds back to the clean original", reset_rows == 248,
          f"{reset_rows} rows")

    print()
    if all(results):
        print("ALL CHECKS PASSED 🎉")
        return 0
    print(f"{results.count(False)} check(s) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
