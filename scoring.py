"""
scoring.py — prioritisation (Section 4 of the build plan / edge case D).

Two independent problems:
  1. BAND — which of the four queues a lead sits in (reply > follow-up > new >
     manual; closed/owned excluded). Processed top-down.
  2. ORDER within a band — one transparent composite score.

The score is stated openly so a rep can defend it:
    0.30 velocity + 0.25 spend + 0.20 stage + 0.15 overdue + 0.05 followers
    + a flat intent bonus when the inbound text asks for a call/bundle/pricing.
Missing metrics score as the median (never as zero), and the £9,000 spend cap is
treated as 'unknown-high' with ties broken on velocity.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

import config
import engine

# Band identifiers (ordered best-first).
BAND_REPLY = "reply_needed"
BAND_FOLLOWUP = "follow_ups_due"
BAND_NEW = "new_outreach"
BAND_MANUAL = "manual_review"
BAND_CLOSED = "closed"
BAND_NONE = ""  # parked / not actionable today
BAND_ORDER = [BAND_REPLY, BAND_FOLLOWUP, BAND_NEW, BAND_MANUAL]


def _has_inbound(lead) -> bool:
    return bool(str(lead.get("last_inbound_text", "")).strip())


def is_reply_needed(lead) -> bool:
    """A genuinely unanswered reply we owe — the hot queue.

    The data stores inbound text on many mid-sequence leads as context, so
    "any inbound" would swamp the band. The discriminating signal is stage: a
    lead sitting at `replied`/`warm` has the ball in our court, and a brand-new
    lead that has already messaged us reached out first (E3). Mid-sequence
    `contacted`/`ghosted` leads keep flowing through follow-ups, with their last
    message surfaced in the Why line for context.
    """
    if not _has_inbound(lead):
        return False
    return lead.get("stage") in ("replied", "warm", "new")


def assign_band(lead, demo_today: date) -> str:
    """Top-down banding (Step 1). Each lead lands in exactly one band."""
    status = lead.get("human_owner_status")
    if status == "closed" or lead.get("stage") in config.CLOSED_STAGES:
        return BAND_CLOSED
    snooze = lead.get("snoozed_until")
    if isinstance(snooze, date) and snooze > demo_today:
        return BAND_NONE  # deferred to a later day via Skip / manual-review Done
    if status == "manager_review":
        return BAND_MANUAL
    if lead.get("first_seen_future"):
        return BAND_NONE  # A5 — not live yet
    if is_reply_needed(lead):
        return BAND_REPLY
    actions = engine.due_actions(lead, demo_today)
    if not actions:
        return BAND_NONE
    started = (int(lead.get("num_touches", 0) or 0) > 0) or lead.get("stage") != "new"
    return BAND_FOLLOWUP if started else BAND_NEW


# --- normalisation helpers ---------------------------------------------------
def _normaliser(series: pd.Series):
    """Return a function v→[0,1] via min-max, with missing→median (A6)."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return lambda v: 0.5
    lo, hi, med = float(vals.min()), float(vals.max()), float(vals.median())
    span = hi - lo

    def norm(v: Optional[float]) -> float:
        x = med if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)
        return 0.5 if span == 0 else (x - lo) / span
    return norm


def _overdue_days(lead, demo_today: date) -> int:
    """How overdue the lead's most-pressing work is (urgency signal)."""
    if _has_inbound(lead):  # owe a reply → measure from our last touch
        anchor = lead.get("last_touch") or lead.get("first_seen")
        if isinstance(anchor, date):
            return max(0, (demo_today - anchor).days)
        return 0
    actions = engine.due_actions(lead, demo_today)
    earliest = None
    for a in actions:
        d = a.get("due_date")
        if isinstance(d, date) and (earliest is None or d < earliest):
            earliest = d
    if earliest is None:
        return 0
    return max(0, (demo_today - earliest).days)


def _intent_bonus(lead) -> float:
    txt = str(lead.get("last_inbound_text", "")).lower()
    return config.INTENT_BONUS if any(k in txt for k in config.INTENT_KEYWORDS) else 0.0


def compute_scores(df: pd.DataFrame, demo_today: date) -> pd.DataFrame:
    """Add `band`, `score`, `overdue_days`, and the human `why` line.

    Scores are computed over the whole frame so normalisation is stable; closed
    leads get band=closed and are simply not ranked into the queues.
    """
    df = df.copy()
    df["band"] = df.apply(lambda r: assign_band(r, demo_today), axis=1)

    n_vel = _normaliser(df["velocity_clean"])
    n_spend = _normaliser(df["spend_clean"])
    n_foll = _normaliser(df["followers_clean"])
    overdue_vals = df.apply(lambda r: _overdue_days(r, demo_today), axis=1)
    n_over = _normaliser(overdue_vals)

    spend_med = pd.to_numeric(df["spend_clean"], errors="coerce").dropna()
    spend_p75 = float(spend_med.quantile(0.75)) if not spend_med.empty else 0.0
    vel_series = pd.to_numeric(df["velocity_clean"], errors="coerce").dropna()
    vel_p75 = float(vel_series.quantile(0.75)) if not vel_series.empty else 0.0

    scores, whys = [], []
    for idx, lead in df.iterrows():
        sw = config.STAGE_WEIGHT.get(lead["stage"], 0.2)
        score = (
            config.SCORE_WEIGHTS["velocity"] * n_vel(lead["velocity_clean"])
            + config.SCORE_WEIGHTS["spend"] * n_spend(lead["spend_clean"])
            + config.SCORE_WEIGHTS["stage"] * sw
            + config.SCORE_WEIGHTS["overdue"] * n_over(overdue_vals[idx])
            + config.SCORE_WEIGHTS["followers"] * n_foll(lead["followers_clean"])
            + _intent_bonus(lead)
        )
        scores.append(round(score, 4))
        whys.append(_why_line(lead, overdue_vals[idx], spend_p75, vel_p75))

    df["overdue_days"] = overdue_vals
    df["score"] = scores
    df["why"] = whys
    return df


def _why_line(lead, overdue: int, spend_p75: float, vel_p75: float) -> str:
    """The most important UI element: the commercial reason, in plain English."""
    bits: list[str] = []
    inbound = str(lead.get("last_inbound_text", "")).strip()
    if inbound:
        sentiment = lead.get("reply_sentiment", "none")
        verb = {"positive": "warm reply", "negative": "objection", "none": "replied"}[sentiment]
        bits.append(f"{verb}: “{inbound[:46]}”")
    elif lead.get("band") == BAND_MANUAL or lead.get("human_owner_status") == "manager_review":
        flags = lead.get("data_quality_flags", []) or []
        if "needs_enrichment" in flags:
            return "no email, phone or handle — can't contact, needs enrichment"
        if "reseller_phone_only" in flags:
            return "reseller with only a phone — not cold-called, needs a human"
        return "data issue — " + ", ".join(flags[:3])
    elif lead.get("stage") == "new":
        bits.append("day-one " + ("shop" if lead.get("lead_type") == "shop" else "reseller"))

    vel = lead.get("velocity_clean")
    spend = lead.get("spend_clean")
    foll = lead.get("followers_clean")
    if foll and lead.get("lead_type") == "reseller":
        bits.append(f"{int(foll/1000)}k followers" if foll >= 1000 else f"{int(foll)} followers")
    if vel is not None and not pd.isna(vel):
        tag = "/mo ★" if vel >= vel_p75 and vel_p75 else "/mo"
        bits.append(f"{int(vel)} sales{tag}")
    if spend is not None and not pd.isna(spend):
        if lead.get("spend_is_cap"):
            bits.append("high spend (£9k+)")
        elif spend >= spend_p75 and spend_p75:
            bits.append(f"£{int(spend/1000)}k+ spend" if spend >= 1000 else f"£{int(spend)} spend")
    touches = int(lead.get("num_touches", 0) or 0)
    if touches and not inbound:
        bits.append(f"{touches} touches")
    if overdue > 0 and not inbound:
        bits.append(f"{overdue}d overdue")
    return " · ".join(bits) if bits else "—"


def order_band(df_band: pd.DataFrame) -> pd.DataFrame:
    """Order within a band by score, tie-break velocity → spend → overdue (Step 2)."""
    if df_band.empty:
        return df_band
    df_band = df_band.copy()
    df_band["_vel"] = pd.to_numeric(df_band["velocity_clean"], errors="coerce").fillna(-1)
    df_band["_spend"] = pd.to_numeric(df_band["spend_clean"], errors="coerce").fillna(-1)
    df_band = df_band.sort_values(
        by=["score", "_vel", "_spend", "overdue_days"],
        ascending=[False, False, False, False],
    ).drop(columns=["_vel", "_spend"])
    return df_band
