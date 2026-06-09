"""
dedup.py — edge-case register section B (B1–B5).

A lead is identified by a canonical ``dedupe_key``:
  * a shop  → store_name + city + country   (A2 — 'Second Threads' in two cities
              stays two leads)
  * a handle-only reseller → the canonical handle (A1 — @driftarchive trap, B4)
  * neither → its own lead_id (never merged)

Merging keeps the most-advanced stage, takes inbound text from the most-recent
touch, locks to the most-recent account, and preserves the loser's detail in
notes (B2/B3). Within-batch duplicates are collapsed first (B1), then the batch
is merged against the existing pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

# Stage ranking for "keep most-advanced" merges. Higher = further along.
STAGE_RANK = {
    "new": 0, "contacted": 1, "ghosted": 2, "replied": 3, "warm": 4,
    "negotiating": 5, "call_booked": 6, "visit_booked": 6, "won": 7, "lost": 3,
}


def dedupe_key(row: pd.Series) -> str:
    """Canonical identity key (A2/A1). Shops by store+city+country, resellers by
    handle, otherwise the lead's own id (so it can never collide)."""
    store = str(row.get("store_clean", "")).strip().lower()
    if store:
        city = str(row.get("city_clean", "")).strip().lower()
        country = str(row.get("country_clean", "")).strip().lower()
        return f"s:{store}|{city}|{country}"
    handle = str(row.get("handle_clean", "")).strip().lower()
    if handle:
        return f"h:{handle}"
    return f"id:{row.get('lead_id', '')}"


def _touch_sort_key(row: pd.Series) -> date:
    """Most-recent signal date for a row (used to pick winners)."""
    lt = row.get("last_touch")
    fs = row.get("first_seen")
    for d in (lt, fs):
        if isinstance(d, date):
            return d
    return date.min


def merge_rows(primary: pd.Series, other: pd.Series) -> pd.Series:
    """Merge ``other`` into ``primary`` per B2/B3 and return the survivor.

    primary is assumed to already be the more-advanced/most-recent of prior
    merges; we still re-check stage rank so order of arrival doesn't matter.
    """
    a, b = primary.copy(), other

    # Most-advanced stage wins.
    if STAGE_RANK.get(b["stage"], 0) > STAGE_RANK.get(a["stage"], 0):
        a["stage"] = b["stage"]

    # Inbound text + account from the most-recent touch.
    if _touch_sort_key(b) >= _touch_sort_key(a):
        if str(b.get("last_inbound_text", "")).strip():
            a["last_inbound_text"] = b["last_inbound_text"]
        if str(b.get("assigned_bdr", "")).strip():
            a["assigned_bdr"] = b["assigned_bdr"]
        if isinstance(b.get("last_touch"), date):
            a["last_touch"] = b["last_touch"]

    # Fill any blank contact fields from the other record (don't overwrite good data).
    for col, valid_col in (("email_clean", "email_valid"), ("phone_clean", "phone_valid")):
        if not str(a.get(col, "")).strip() and str(b.get(col, "")).strip():
            a[col] = b[col]
            a[valid_col] = b.get(valid_col, False)
    for col in ("handle_clean", "store_clean", "city_clean", "country_clean",
                "contact_name", "followers_clean", "velocity_clean", "spend_clean"):
        if not str(a.get(col, "")).strip() and str(b.get(col, "")).strip():
            a[col] = b[col]

    # Keep the higher touch count.
    a["num_touches"] = max(int(a.get("num_touches", 0)), int(b.get("num_touches", 0)))

    # Preserve the loser's detail in notes (B2/B3) — never silently drop it.
    note = f"[merged {b.get('lead_id', '?')}: stage={b.get('stage')}"
    if str(b.get("assigned_bdr", "")).strip():
        note += f", bdr={b.get('assigned_bdr')}"
    if str(b.get("last_inbound_text", "")).strip():
        note += f", said='{str(b.get('last_inbound_text'))[:40]}'"
    note += "]"
    a["notes"] = (str(a.get("notes", "")).strip() + " " + note).strip()

    flags = list(a.get("data_quality_flags", []) or [])
    if "merged_duplicate" not in flags:
        flags.append("merged_duplicate")
    a["data_quality_flags"] = flags
    return a


def _pick_primary(group: pd.DataFrame) -> pd.Series:
    """Choose the survivor of a duplicate group: most-advanced, then most-recent."""
    best_idx = group.index[0]
    best = group.loc[best_idx]
    for idx in group.index[1:]:
        cand = group.loc[idx]
        better_stage = STAGE_RANK.get(cand["stage"], 0) > STAGE_RANK.get(best["stage"], 0)
        same_stage = STAGE_RANK.get(cand["stage"], 0) == STAGE_RANK.get(best["stage"], 0)
        if better_stage or (same_stage and _touch_sort_key(cand) > _touch_sort_key(best)):
            best_idx, best = idx, cand
    return best


def dedupe_within(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """B1 — collapse duplicates inside a single batch before anything else.

    Returns (deduped_df, n_collapsed).
    """
    df = df.copy()
    df["dedupe_key"] = df.apply(dedupe_key, axis=1)
    survivors = []
    collapsed = 0
    for _, group in df.groupby("dedupe_key", sort=False):
        if len(group) == 1:
            survivors.append(group.iloc[0])
            continue
        primary = _pick_primary(group)
        for idx in group.index:
            if idx == primary.name:
                continue
            primary = merge_rows(primary, group.loc[idx])
            collapsed += 1
        survivors.append(primary)
    out = pd.DataFrame(survivors).reset_index(drop=True)
    return out, collapsed


@dataclass
class MergeStats:
    added: int = 0
    skipped: int = 0      # key already in pipeline, new row adds nothing material
    merged: int = 0       # key already in pipeline, new row contributed info
    within_batch: int = 0
    added_keys: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    merged_ids: list[str] = field(default_factory=list)


def _adds_information(new_row: pd.Series, existing: pd.Series) -> bool:
    """Does the incoming row contribute anything beyond what we already hold?

    If not, it is a pure 'already in pipeline' skip (the @driftarchive case: a
    barren 'new' duplicate of a lead we've already engaged).
    """
    if STAGE_RANK.get(new_row["stage"], 0) > STAGE_RANK.get(existing["stage"], 0):
        return True
    if str(new_row.get("last_inbound_text", "")).strip() and \
            not str(existing.get("last_inbound_text", "")).strip():
        return True
    for col in ("email_clean", "phone_clean"):
        if str(new_row.get(col, "")).strip() and not str(existing.get(col, "")).strip():
            return True
    return False


def merge_batch(existing: pd.DataFrame, new_batch: pd.DataFrame) -> tuple[pd.DataFrame, MergeStats]:
    """Merge a (already within-batch-deduped) batch into the existing pipeline.

    B4: a day-2 'new' lead whose key already exists is SKIPPED — never re-added as
    a fresh DM. If it brings new info the existing lead is enriched in place
    (most-advanced stage preserved, so we never downgrade 'replied' → 'new').
    """
    existing = existing.copy()
    if "dedupe_key" not in existing.columns:
        existing["dedupe_key"] = existing.apply(dedupe_key, axis=1)
    key_to_idx = {k: i for i, k in zip(existing.index, existing["dedupe_key"])}

    stats = MergeStats()
    for _, nrow in new_batch.iterrows():
        key = nrow["dedupe_key"]
        if key in key_to_idx:
            idx = key_to_idx[key]
            if _adds_information(nrow, existing.loc[idx]):
                existing.loc[idx] = merge_rows(existing.loc[idx], nrow)
                stats.merged += 1
                stats.merged_ids.append(str(nrow["lead_id"]))
            else:
                # B4 — already in pipeline, nothing new → skip (no re-DM).
                stats.skipped += 1
                stats.skipped_ids.append(str(nrow["lead_id"]))
        else:
            existing = pd.concat([existing, nrow.to_frame().T], ignore_index=True)
            key_to_idx[key] = existing.index[-1]
            stats.added += 1
            stats.added_keys.append(key)
    return existing.reset_index(drop=True), stats
