"""
accounts.py — the IG-account capacity layer and its guards (Section 9, F1–F7).

DMs are capped per active account (default 40/day). After ranking, the cap is
applied PER ACCOUNT: fill each account's remaining slots with its highest-scored
DM leads, follow-ups before new. Overflow rolls to tomorrow — never reassigned to
another account, because that would switch the sender mid-conversation.

This is deliberately a secondary layer: the demo runs on one account at a 40 cap;
the same code scales to N accounts. Email and phone have no cap.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config

ACTIVE = "active"
FOLLOWUPS_ONLY = "followups_only"
PAUSED = "paused"


def default_state() -> dict:
    return {
        "accounts": [
            {"id": "Account 1", "handle": "fleek_main", "status": ACTIVE,
             "cap": config.DM_DAILY_LIMIT, "sent_today": 0},
        ],
        "demo_today": None,
    }


def load_accounts() -> dict:
    if not os.path.exists(config.ACCOUNTS_STATE):
        state = default_state()
        save_accounts(state)
        return state
    try:
        with open(config.ACCOUNTS_STATE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default_state()


def save_accounts(state: dict) -> None:
    tmp = config.ACCOUNTS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, config.ACCOUNTS_STATE)  # atomic


def roll_day_if_needed(state: dict, demo_today: str) -> dict:
    """G3 — when DEMO_TODAY rolls over, reset per-account sent_today counts.

    Leads sent under the old day stay sent; only the daily counter resets.
    """
    if state.get("demo_today") != demo_today:
        for acc in state["accounts"]:
            acc["sent_today"] = 0
        state["demo_today"] = demo_today
        save_accounts(state)
    return state


def active_accounts(state: dict) -> list[dict]:
    return [a for a in state["accounts"] if a["status"] in (ACTIVE, FOLLOWUPS_ONLY)]


def remaining_slots(acc: dict) -> int:
    """F-guard: never negative, even if more were sent than the cap allows."""
    return max(0, int(acc["cap"]) - int(acc.get("sent_today", 0)))


def overage(acc: dict) -> int:
    """How many were sent beyond cap (surfaced honestly, e.g. a 41st manual DM)."""
    return max(0, int(acc.get("sent_today", 0)) - int(acc["cap"]))


# --- assignment --------------------------------------------------------------
def assign_dm_accounts(df: pd.DataFrame, state: dict) -> pd.DataFrame:
    """Round-robin assign/reassign an account to DM leads.

    In-progress leads (assigned_instagram_account_id set — first DM was sent) are
    locked and never moved (F2). Queued leads (no assigned_instagram_account_id)
    are round-robined across ACTIVE accounts, naturally rebalancing when the
    account list changes (e.g. a new account is added).
    """
    df = df.copy()
    if "assigned_account" not in df.columns:
        df["assigned_account"] = ""
    if "planned_instagram_account_id" not in df.columns:
        df["planned_instagram_account_id"] = ""
    if "assigned_instagram_account_id" not in df.columns:
        df["assigned_instagram_account_id"] = ""

    # Backfill: leads that had a DM sent before these columns were introduced
    if "dm_step" in df.columns:
        needs_backfill = (
            (df["assigned_instagram_account_id"].astype(str).str.strip() == "") &
            (df["dm_step"].astype(int) > 0) &
            (df["assigned_account"].astype(str).str.strip() != "")
        )
        df.loc[needs_backfill, "assigned_instagram_account_id"] = df.loc[needs_backfill, "assigned_account"]

    open_accounts = [a["id"] for a in state["accounts"] if a["status"] == ACTIVE]
    if not open_accounts:
        return df

    rr = 0
    for idx in df.index:
        if not bool(df.at[idx, "dm_active"]):
            continue
        # In-progress leads are locked to their account
        if str(df.at[idx, "assigned_instagram_account_id"]).strip():
            continue
        # Queued: round-robin across active accounts (rebalances when list changes)
        acc_id = open_accounts[rr % len(open_accounts)]
        df.at[idx, "planned_instagram_account_id"] = acc_id
        df.at[idx, "assigned_account"] = acc_id
        rr += 1
    return df


def in_progress_count(df: pd.DataFrame, account_id: str) -> int:
    """Leads with first DM sent, owned by this account, not closed."""
    if "assigned_instagram_account_id" not in df.columns:
        return 0
    mask = df["assigned_instagram_account_id"].astype(str).str.strip() == str(account_id)
    if "band" in df.columns:
        mask = mask & (df["band"].astype(str) != "closed")
    return int(mask.sum())


def queued_count(df: pd.DataFrame, account_id: str) -> int:
    """Leads planned for this account but not yet sent (no DM out yet)."""
    if "planned_instagram_account_id" not in df.columns:
        return 0
    planned = df["planned_instagram_account_id"].astype(str).str.strip() == str(account_id)
    not_assigned = (
        df["assigned_instagram_account_id"].astype(str).str.strip() == ""
        if "assigned_instagram_account_id" in df.columns
        else pd.Series([True] * len(df), index=df.index)
    )
    mask = planned & not_assigned
    if "band" in df.columns:
        mask = mask & (df["band"].astype(str) != "closed")
    return int(mask.sum())


def needs_dm_slot(lead) -> bool:
    """Does working this lead today consume a DM send on its account?"""
    if not bool(lead.get("dm_active")):
        return False
    if lead.get("band") not in ("reply_needed", "follow_ups_due", "new_outreach"):
        return False
    # A DM reply (reply-needed on the DM channel) also consumes a slot.
    if lead.get("band") == "reply_needed":
        ch = lead.get("conversation_channel") or lead.get("primary_channel")
        return ch == "dm"
    return bool(lead.get("_dm_action_due"))


@dataclass
class CapacityResult:
    sendable_ids: set
    rolled_ids: set
    per_account: dict  # id -> {sent, cap, left, status, assigned, sendable, rolled, over}


def apply_capacity(df: pd.DataFrame, state: dict) -> CapacityResult:
    """Plan today's DM sends within each account's remaining capacity.

    df must already carry band, score and assigned_account. Returns which DM
    leads are sendable today vs. rolled, plus a per-account summary for the board.
    """
    per_account: dict = {}
    sendable, rolled = set(), set()

    status_by_id = {a["id"]: a for a in state["accounts"]}
    dm_df = df[df.apply(needs_dm_slot, axis=1)].copy()

    for acc in state["accounts"]:
        aid = acc["id"]
        mine = dm_df[dm_df["assigned_account"] == aid].copy()
        # Follow-ups (incl. replies) before new outreach, then by score.
        mine["_is_followup"] = mine["band"].isin(["reply_needed", "follow_ups_due"])
        mine = mine.sort_values(by=["_is_followup", "score"],
                                ascending=[False, False])

        if acc["status"] == PAUSED:
            slots = 0
            eligible = mine                      # all blocked → roll/reassign
        elif acc["status"] == FOLLOWUPS_ONLY:
            slots = remaining_slots(acc)
            eligible = mine[mine["_is_followup"]]
            # new-outreach DMs on a followups-only account roll over
            for lid in mine[~mine["_is_followup"]]["lead_id"]:
                rolled.add(lid)
        else:  # ACTIVE
            slots = remaining_slots(acc)
            eligible = mine

        chosen = eligible.head(slots)
        for lid in chosen["lead_id"]:
            sendable.add(lid)
        for lid in eligible["lead_id"]:
            if lid not in sendable:
                rolled.add(lid)

        per_account[aid] = {
            "id": aid,
            "status": acc["status"],
            "sent": int(acc.get("sent_today", 0)),
            "cap": int(acc["cap"]),
            "left": remaining_slots(acc) if acc["status"] != PAUSED else 0,
            "over": overage(acc),
            "assigned": int(len(mine)),
            "sendable": int(len(chosen)) if acc["status"] != PAUSED else 0,
            "rolled": int(len(mine) - (len(chosen) if acc["status"] != PAUSED else 0)),
        }
    return CapacityResult(sendable, rolled, per_account)


def record_dm_sent(state: dict, account_id: str) -> dict:
    """Increment an account's sent counter (F: records a 41st over-cap send too)."""
    for acc in state["accounts"]:
        if acc["id"] == account_id:
            acc["sent_today"] = int(acc.get("sent_today", 0)) + 1
            break
    save_accounts(state)
    return state


# --- guards (F1–F7), data side ----------------------------------------------
def can_delete_account(df: pd.DataFrame, account_id: str) -> tuple[bool, int]:
    """F5 — block deleting an account that still owns live conversations."""
    if "assigned_account" not in df.columns:
        return True, 0
    live = df[(df["assigned_account"] == account_id)
              & (df["band"].isin(["reply_needed", "follow_ups_due"]))]
    n = int(len(live))
    return (n == 0), n


def blocked_followups(df: pd.DataFrame, state: dict) -> int:
    """F: follow-ups stuck because their account is paused (banner, not dead tasks)."""
    paused = {a["id"] for a in state["accounts"] if a["status"] == PAUSED}
    if not paused or "assigned_account" not in df.columns:
        return 0
    stuck = df[(df["assigned_account"].isin(paused))
               & (df["band"].isin(["reply_needed", "follow_ups_due"]))
               & (df["dm_active"])]
    return int(len(stuck))
