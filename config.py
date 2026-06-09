"""
config.py — all tunable constants for the Fleek pipeline tool.

Everything here is hardcoded by design (Section 11 of the build plan): an agent
should not have to configure the brain. The single value the brief calls out as
legitimately changeable is DM_DAILY_LIMIT, which the UI exposes as a setting.

DEMO_TODAY is computed as max(parsed dates across the source data) + 1 day so the
demo never looks "all overdue". It is resolved lazily from the data layer and
cached; a fixed fallback (2026-03-01) is used if no dates can be parsed.
"""

from __future__ import annotations

import os
from datetime import date

# --- Paths -------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Raw source files (read-only anchors shipped with the repo).
SOURCE_PIPELINE = os.path.join(DATA_DIR, "pipeline.csv")
SOURCE_DAY2 = os.path.join(DATA_DIR, "new_drop_day2.csv")

# Live state written by the app.
LIVE_PIPELINE = os.path.join(DATA_DIR, "pipeline_live.csv")
# Frozen cleaned copy used as the reset anchor (Section 12).
CLEAN_ORIGINAL = os.path.join(DATA_DIR, "pipeline_clean_original.csv")
# Accounts state (capacity layer).
ACCOUNTS_STATE = os.path.join(DATA_DIR, "accounts_state.json")
# Staging file for atomic uploads (I1).
STAGING_PIPELINE = os.path.join(DATA_DIR, "_staging_pipeline.csv")

LOG_FILE = os.path.join(BASE_DIR, "fleek_pipeline.log")

# --- Demo clock --------------------------------------------------------------
# Fallback only; the real value is max(parsed dates) + 1 day (see data_store).
DEMO_TODAY_FALLBACK = date(2026, 3, 1)

# --- Sequencing (Section 3) --------------------------------------------------
DM_DAILY_LIMIT = 40              # PER active IG account
DM_FOLLOWUP_DAYS = [3, 4, 5]     # offsets for DM 2, 3, 4 (after DM 1 on day 0)
EMAIL_FOLLOWUP_DAYS = [3, 4, 5]  # offsets for Email 2, 3, 4
COLD_CALL_GAP_DAYS = 2           # cold call 2 fires this many days after cold call 1
DM_MAX_STEP = 4
EMAIL_MAX_STEP = 4
CALL_MAX_STEP = 2                # cold call 1 + cold call 2 (warm call is triggered)

BOOKING_LINK = "cal.ly/fleek"

# --- Scoring (Section 4) -----------------------------------------------------
SCORE_WEIGHTS = {
    "velocity": 0.30,   # strongest buying signal
    "spend": 0.25,      # commercial value
    "stage": 0.20,      # how warm
    "overdue": 0.15,    # urgency / don't go cold
    "followers": 0.05,  # reach (weakest)
}

STAGE_WEIGHT = {
    "negotiating": 1.0,
    "warm": 0.8,
    "replied": 0.7,
    "contacted": 0.4,
    "ghosted": 0.3,
    "new": 0.2,
}

# Inbound intent keywords → a big flat add that overrides cold-but-big leads.
INTENT_KEYWORDS = ["call", "bundle", "pricing", "price", "keen", "commission", "quote"]
INTENT_BONUS = 0.40

# A spend of exactly £9,000 is a data cap meaning "unknown-high", not a true 9000
# (edge case A3). We keep the value for ranking but tie-break on velocity.
SPEND_CAP_SENTINEL = 9000

# --- Canonical stage vocabulary ----------------------------------------------
# Maps every messy stage string we saw in the data to a canonical stage.
STAGE_CANON = {
    "new": "new", "new lead": "new",
    "contacted": "contacted",
    "reply": "replied", "replied": "replied",
    "warm": "warm",
    "negotiating": "negotiating", "in negotiation": "negotiating",
    "ghosted": "ghosted", "no response": "ghosted",
    "call booked": "call_booked", "call-booked": "call_booked",
    "visit booked": "visit_booked", "visit-booked": "visit_booked",
    "won": "won", "closed won": "won",
    "lost": "lost", "closed lost": "lost",
}

# Stages where a human already owns the lead — EXCLUDED from the task list.
CLOSED_STAGES = {"won", "lost", "call_booked", "visit_booked", "negotiating"}
# Stages that count as "conversation started" (vs. brand-new).
CONTACTED_STAGES = {"contacted", "replied", "warm", "ghosted"}

# --- Sources -----------------------------------------------------------------
# Source strings that imply a reseller (IG-led) vs. a physical shop.
RESELLER_SOURCES = {
    "depop", "vinted", "whatnot", "ebay", "ig", "instagram",
    "instagram_dm",
}
SHOP_SOURCES = {
    "store", "physical store", "in-person", "google_maps",
}

# --- Reply outcomes (Section 6) ----------------------------------------------
OUTCOME_KEEP = "keep_talking"
OUTCOME_CALL_BOOKED = "call_booked"
OUTCOME_VISIT_BOOKED = "visit_booked"
OUTCOME_WON = "won"
OUTCOME_LOST = "lost"
BOOKING_OUTCOMES = {OUTCOME_CALL_BOOKED, OUTCOME_VISIT_BOOKED, OUTCOME_WON}
TERMINAL_OUTCOMES = {OUTCOME_CALL_BOOKED, OUTCOME_VISIT_BOOKED, OUTCOME_WON, OUTCOME_LOST}

# --- LLM ---------------------------------------------------------------------
OPENAI_MODEL = "gpt-4o-mini"


def openai_api_key() -> str | None:
    """API key from env var or Streamlit secret; never committed."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:  # Streamlit secret, only if streamlit is importable and a secret exists.
        import streamlit as st  # noqa: WPS433 (local import on purpose)

        return st.secrets.get("OPENAI_API_KEY")  # type: ignore[no-any-return]
    except Exception:  # pragma: no cover - secrets not configured / no streamlit
        return None
