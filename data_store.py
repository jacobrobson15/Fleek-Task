"""
data_store.py — the single data layer. Every read/write of the pipeline goes
through here, so the CSV backing can later be swapped for Postgres/SQLite with no
change to the brain (Section 2). Writes are atomic: stage → validate → replace,
never a half-written live file (I1).

It also owns the demo clock: DEMO_TODAY = max(parsed source dates) + 1 day, fixed
so the demo isn't "all overdue".
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import classify
import cleaning
import config
import dedup
import engine

# --- column type map (for CSV (de)serialisation) -----------------------------
DATE_COLS = ["first_seen", "last_touch", "dm_next_date", "email_next_date",
             "call_next_date", "snoozed_until"]
LIST_COLS = ["data_quality_flags"]
BOOL_COLS = ["email_valid", "phone_valid", "spend_is_cap", "first_seen_future",
             "last_touch_reliable", "dm_active", "email_active", "call_active",
             "warm_call_eligible", "warm_call_due", "tracks_initialized"]
FLOAT_COLS = ["spend_clean", "followers_clean", "velocity_clean", "listings_clean",
              "avg_price_clean"]
INT_COLS = ["num_touches", "dm_step", "email_step", "call_step", "sequence_step"]


_DEMO_TODAY: Optional[date] = None


def get_demo_today() -> date:
    """max parsed date across the ORIGINAL source files + 1 day, cached."""
    global _DEMO_TODAY
    if _DEMO_TODAY is not None:
        return _DEMO_TODAY
    dates: list[date] = []
    for path in (config.SOURCE_PIPELINE, config.SOURCE_DAY2):
        if not os.path.exists(path):
            continue
        raw = pd.read_csv(path, dtype=str).fillna("")
        for col in ("first_seen_date", "last_touch_date"):
            for v in raw.get(col, []):
                d = cleaning.parse_date(v)
                if d:
                    dates.append(d)
    _DEMO_TODAY = (max(dates) + timedelta(days=1)) if dates else config.DEMO_TODAY_FALLBACK
    return _DEMO_TODAY


# --- (de)serialisation -------------------------------------------------------
def _serialise(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in DATE_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda d: d.isoformat() if isinstance(d, date) else "")
    for col in LIST_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda v: json.dumps(v) if isinstance(v, list) else (v or "[]"))
    for col in BOOL_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda b: "True" if bool(b) else "False")
    return out


def _deserialise(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().fillna("")
    for col in DATE_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda s: cleaning.parse_date(s) if str(s).strip() else None)
    for col in LIST_COLS:
        if col in out.columns:
            out[col] = out[col].apply(_parse_list)
    for col in BOOL_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda s: str(s).strip().lower() in ("true", "1", "yes"))
    for col in FLOAT_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda s: float(s) if str(s).strip() not in ("", "nan") else None)
    for col in INT_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda s: int(float(s)) if str(s).strip() not in ("", "nan") else 0)
    return out


def _parse_list(v):
    if isinstance(v, list):
        return v
    s = str(v).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [s]


# --- build / load / save -----------------------------------------------------
def build_clean_pipeline() -> pd.DataFrame:
    """Clean + within-batch dedupe + classify + init the original pipeline."""
    today = get_demo_today()
    raw = pd.read_csv(config.SOURCE_PIPELINE, dtype=str)
    df = cleaning.clean_dataframe(raw, today)
    df, _ = dedup.dedupe_within(df)
    df = classify.classify_dataframe(df)
    df = engine.init_tracks(df, today)
    df["dedupe_key"] = df.apply(dedup.dedupe_key, axis=1)
    return df


def ensure_initialized() -> None:
    """Create the frozen clean anchor and the live file on first run."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(config.CLEAN_ORIGINAL):
        df = build_clean_pipeline()
        save(df, config.CLEAN_ORIGINAL)
    if not os.path.exists(config.LIVE_PIPELINE):
        shutil.copyfile(config.CLEAN_ORIGINAL, config.LIVE_PIPELINE)


def load() -> pd.DataFrame:
    ensure_initialized()
    raw = pd.read_csv(config.LIVE_PIPELINE, dtype=str)
    return _deserialise(raw)


def save(df: pd.DataFrame, path: Optional[str] = None) -> None:
    """Atomic write (I1): serialise → temp file → os.replace."""
    path = path or config.LIVE_PIPELINE
    tmp = path + ".tmp"
    _serialise(df).to_csv(tmp, index=False)
    os.replace(tmp, path)  # atomic on POSIX — never a half-written live file


def reset() -> None:
    """Wind the live pipeline back to the frozen clean anchor (Section 12)."""
    ensure_initialized()
    shutil.copyfile(config.CLEAN_ORIGINAL, config.LIVE_PIPELINE)


def export_path() -> str:
    """Path to the current live CSV for a one-click manager download."""
    ensure_initialized()
    return config.LIVE_PIPELINE


def clean_external(raw: pd.DataFrame) -> pd.DataFrame:
    """Clean + classify + init an uploaded batch (within-batch dedupe included)."""
    today = get_demo_today()
    df = cleaning.clean_dataframe(raw, today)
    df, within = dedup.dedupe_within(df)
    df = classify.classify_dataframe(df)
    df = engine.init_tracks(df, today)
    df["dedupe_key"] = df.apply(dedup.dedupe_key, axis=1)
    df.attrs["within_batch_collapsed"] = within
    return df
