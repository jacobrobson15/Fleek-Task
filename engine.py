"""
engine.py — the per-track sequence engine (edge cases E1–E4). This is the heart.

Each lead can have up to three tracks running in parallel:
  * DM    (resellers; or a shop reachable only by handle)  — 1 cold + 3 follow-ups
  * Email (shops, and resellers who have an email)         — 4 steps
  * Cold call (shops with a phone; resellers are NEVER cold-called) — 2 steps
plus a triggered Warm call that fires on a positive reply when a phone exists.

`step` = touches completed on that track. `*_next_date` = when the next step is
due. A booking outcome on ANY channel ends every track (E1) so no contradictory
message can ever go out after a win.

All functions mutate a single lead row (a dict-like / pd.Series) in place and are
safe to call repeatedly — advancing today's track twice is a no-op (I2), enforced
together with the action-hash guard in logic.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

import config
from cleaning import parse_date

# --- sentiment keywords ------------------------------------------------------
_POSITIVE = [
    "call", "bundle", "pricing", "price", "keen", "yes", "interested", "send",
    "book", "fri", "mon", "tue", "wed", "thu", "sounds good", "let's", "lets",
    "happy to chat", "quote", "commission", "how much",
]
_NEGATIVE = [
    "not interested", "no thanks", "no thank", "another platform", "already on",
    "stop", "unsubscribe", "remove me", "not for us", "pass", "leave me",
]


def classify_sentiment(text: object) -> str:
    """Map inbound text → positive / negative / none."""
    s = str(text or "").strip().lower()
    if not s:
        return "none"
    if any(k in s for k in _NEGATIVE):
        return "negative"
    if any(k in s for k in _POSITIVE):
        return "positive"
    return "none"


# --- scheduling --------------------------------------------------------------
def _anchor_date(lead, demo_today: date) -> date:
    """The date outreach is measured from for an existing lead.

    Uses last_touch when reliable (A4), else first_seen, else today.
    """
    lt = lead.get("last_touch")
    if isinstance(lt, date) and lead.get("last_touch_reliable", True):
        return lt
    fs = lead.get("first_seen")
    if isinstance(fs, date):
        return fs
    return demo_today


def _gap_after(channel: str, step_completed: int) -> Optional[int]:
    """Days until the next step after completing `step_completed` on a channel.

    Returns None when the track is finished (no further step).
    """
    if channel == "dm":
        offsets = config.DM_FOLLOWUP_DAYS          # gaps for DM2, DM3, DM4
        if 1 <= step_completed <= len(offsets):
            return offsets[step_completed - 1]
        return None
    if channel == "email":
        offsets = config.EMAIL_FOLLOWUP_DAYS
        if 1 <= step_completed <= len(offsets):
            return offsets[step_completed - 1]
        return None
    if channel == "call":
        return config.COLD_CALL_GAP_DAYS if step_completed == 1 else None
    return None


def _max_step(channel: str) -> int:
    return {"dm": config.DM_MAX_STEP, "email": config.EMAIL_MAX_STEP,
            "call": config.CALL_MAX_STEP}[channel]


# --- initialisation ----------------------------------------------------------
def init_tracks(df: pd.DataFrame, demo_today: date) -> pd.DataFrame:
    """Seed track state for rows not yet initialised. Idempotent: a row already
    carrying `tracks_initialized == True` is left untouched (its live progress
    persists across reloads, G1/G3)."""
    df = df.copy()
    for col, default in (
        ("dm_step", 0), ("email_step", 0), ("call_step", 0),
        ("dm_next_date", None), ("email_next_date", None), ("call_next_date", None),
        ("warm_call_due", False), ("reply_sentiment", "none"),
        ("sequence_step", 0), ("tracks_initialized", False),
        ("last_action_key", ""),
    ):
        if col not in df.columns:
            df[col] = default

    for idx in df.index:
        if bool(df.at[idx, "tracks_initialized"]):
            continue
        lead = df.loc[idx]
        state = _initial_state(lead, demo_today)
        for k, v in state.items():
            df.at[idx, k] = v
        df.at[idx, "tracks_initialized"] = True
    return df


def _initial_state(lead, demo_today: date) -> dict:
    """Compute the opening track state for a freshly cleaned lead."""
    stage = lead.get("stage", "new")
    closed = stage in config.CLOSED_STAGES
    sentiment = classify_sentiment(lead.get("last_inbound_text"))
    state = {
        "dm_step": 0, "email_step": 0, "call_step": 0,
        "dm_next_date": None, "email_next_date": None, "call_next_date": None,
        "warm_call_due": False, "reply_sentiment": sentiment,
    }
    if closed:
        return state  # owned by a human — no tracks run.

    num_touches = int(lead.get("num_touches", 0) or 0)
    anchor = _anchor_date(lead, demo_today)
    primary = lead.get("primary_channel")
    engaged = stage in ("replied", "warm")  # they've spoken to us

    def schedule(channel: str, step: int):
        """Set step + next_date for one track given a completed step count."""
        mx = _max_step(channel)
        step = min(step, mx)
        state[f"{channel}_step"] = step
        gap = _gap_after(channel, step)
        if step >= mx or gap is None:
            if step == 0:                       # brand-new: first touch due today
                state[f"{channel}_next_date"] = demo_today
            else:
                state[f"{channel}_next_date"] = None   # track exhausted/parked
        else:
            nxt = (anchor + timedelta(days=gap)) if step >= 1 else demo_today
            state[f"{channel}_next_date"] = nxt

    if num_touches == 0 and stage == "new":
        # Day-zero: every eligible track starts today, in parallel (E4).
        if lead.get("dm_active"):
            schedule("dm", 0)
        if lead.get("email_active"):
            schedule("email", 0)
        if lead.get("call_active"):
            schedule("call", 0)
        return state

    # Existing conversation: the primary channel carries the touch history.
    if primary in ("dm", "email", "call") and lead.get(f"{primary}_active"):
        schedule(primary, num_touches)
    else:
        # primary not a sequence channel (e.g. manual review) — start what we can.
        for ch in ("dm", "email", "call"):
            if lead.get(f"{ch}_active"):
                schedule(ch, num_touches)
                break

    if engaged:
        # Warm lead: cold-calling is moot; a positive reply arms the warm call.
        if sentiment == "positive" and lead.get("warm_call_eligible"):
            state["warm_call_due"] = True
        state["call_next_date"] = None  # never cold-call someone already talking
    return state


# --- live advancement --------------------------------------------------------
def advance_track(lead, channel: str, demo_today: date) -> None:
    """Mark one step of `channel` sent: bump step, schedule next, +1 touch."""
    if channel == "warm":
        lead["warm_call_due"] = False
        lead["last_touch"] = demo_today
        lead["num_touches"] = int(lead.get("num_touches", 0)) + 1
        return
    step = int(lead.get(f"{channel}_step", 0)) + 1
    mx = _max_step(channel)
    step = min(step, mx)
    lead[f"{channel}_step"] = step
    gap = _gap_after(channel, step)
    lead[f"{channel}_next_date"] = None if (gap is None or step >= mx) \
        else demo_today + timedelta(days=gap)
    lead["last_touch"] = demo_today
    lead["num_touches"] = int(lead.get("num_touches", 0)) + 1
    lead["sequence_step"] = int(lead.get("sequence_step", 0)) + 1
    # Advancing past a 'new' stage means the conversation has started.
    if lead.get("stage") == "new":
        lead["stage"] = "contacted"


def end_all_tracks(lead) -> None:
    """E1 — terminate every track so nothing further is ever scheduled/sent."""
    for ch in ("dm", "email", "call"):
        lead[f"{ch}_step"] = _max_step(ch)
        lead[f"{ch}_next_date"] = None
    lead["warm_call_due"] = False


def apply_outcome(lead, outcome: str, channel: str, demo_today: date,
                  inbound_text: str = "") -> None:
    """Apply a Reply outcome to a lead and reconcile all tracks (E1–E3, H).

    channel is where the reply landed (conversation_channel locks to it, E3).
    """
    if inbound_text:
        # H1: latest inbound becomes current; prior is preserved in notes.
        prev = str(lead.get("last_inbound_text", "")).strip()
        if prev and prev != inbound_text:
            lead["notes"] = (str(lead.get("notes", "")).strip()
                             + f" [prev reply: '{prev[:40]}']").strip()
        lead["last_inbound_text"] = inbound_text
    if channel:
        lead["conversation_channel"] = channel

    if outcome in config.BOOKING_OUTCOMES:
        # Booking on ANY channel → close everything immediately (E1).
        end_all_tracks(lead)
        lead["stage"] = {
            config.OUTCOME_CALL_BOOKED: "call_booked",
            config.OUTCOME_VISIT_BOOKED: "visit_booked",
            config.OUTCOME_WON: "won",
        }[outcome]
        lead["human_owner_status"] = "closed"
        lead["reply_sentiment"] = "positive"
        return

    if outcome == config.OUTCOME_LOST:
        end_all_tracks(lead)
        lead["stage"] = "lost"
        lead["human_owner_status"] = "closed"
        lead["reply_sentiment"] = "negative"
        return

    # keep_talking == a positive reply that isn't (yet) a booking (E2/E3).
    lead["reply_sentiment"] = "positive"
    if lead.get("stage") in ("new", "contacted", "ghosted"):
        lead["stage"] = "replied"
    # Pause cold tracks; the conversation is live now.
    lead["call_next_date"] = None
    if lead.get("warm_call_eligible"):
        lead["warm_call_due"] = True            # warm call fires (E2 has phone)
    # E2: no phone → warm message stays on the engaged channel; its follow-up
    # schedule already covers that, nothing to cancel.


def clear_reply_needed(lead, demo_today: date) -> None:
    """Rep answered an inbound: archive it and resume the cadence."""
    txt = str(lead.get("last_inbound_text", "")).strip()
    if txt:
        lead["notes"] = (str(lead.get("notes", "")).strip()
                         + f" [answered: '{txt[:40]}']").strip()
    lead["last_inbound_text"] = ""
    # Count the reply we just sent as a touch on the engaged channel.
    ch = lead.get("conversation_channel") or lead.get("primary_channel")
    if ch in ("dm", "email", "call") and lead.get(f"{ch}_active"):
        advance_track(lead, ch, demo_today)
    else:
        lead["last_touch"] = demo_today
        lead["num_touches"] = int(lead.get("num_touches", 0)) + 1


# --- reading due work --------------------------------------------------------
def due_actions(lead, demo_today: date) -> list[dict]:
    """All actions due today across every track, in display order.

    Reply-needed is handled by the band layer (it owns the inbound). Here we
    surface scheduled outreach: warm call first, then follow-ups/new touches.
    """
    if lead.get("human_owner_status") == "closed" or lead.get("stage") in config.CLOSED_STAGES:
        return []
    if lead.get("first_seen_future"):
        return []  # A5 — not yet live

    actions: list[dict] = []
    if bool(lead.get("warm_call_due")):
        actions.append({
            "channel": "warm", "kind": "warm_call", "step": 0,
            "label": "Warm call", "due_date": demo_today,
        })

    for channel, kind in (("dm", "DM"), ("email", "Email"), ("call", "Cold call")):
        if not lead.get(f"{channel}_active"):
            continue
        step = int(lead.get(f"{channel}_step", 0))
        if step >= _max_step(channel):
            continue
        nxt = lead.get(f"{channel}_next_date")
        nxt = parse_date(nxt) if not isinstance(nxt, date) and nxt else nxt
        if isinstance(nxt, date) and nxt <= demo_today:
            next_step = step + 1
            first = step == 0
            if channel == "call":
                label = f"Cold call {next_step}"
            elif first:
                label = "DM — first touch" if channel == "dm" else "Email — intro"
            else:
                label = f"{kind} {next_step} — follow-up"
            actions.append({
                "channel": channel, "kind": channel, "step": step,
                "label": label, "due_date": nxt, "is_first": first,
            })
    return actions
