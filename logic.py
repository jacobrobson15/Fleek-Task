"""
logic.py — the brain's public facade. app.py talks ONLY to this module.

It orchestrates the pipeline (clean → dedupe → classify → track-engine → score →
capacity → draft) and exposes the handful of verbs the UI needs:

    build_state()       assemble today's progress board + four-band card list
    mark_sent()         advance a track one step (idempotent, I2)
    save_reply()        apply a reply outcome (closes tracks on a booking, E1)
    skip() / undo_last()
    upload_batch()      dedupe + merge a new drop, atomically (Section 8, I1)
    reset_pipeline() / export_path()
    account_* helpers   the secondary capacity layer + its guards (Section 9)

No Streamlit imports live here — the brain stays headless and testable.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import accounts
import config
import data_store
import dedup
import drafting
import engine
import scoring

logging.basicConfig(
    filename=config.LOG_FILE, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("fleek")

# How many cards to render per band before collapsing to "showing top N" (G2).
VISIBLE_CAP = 60

BAND_LABELS = {
    scoring.BAND_REPLY: "REPLY NEEDED",
    scoring.BAND_FOLLOWUP: "FOLLOW-UPS DUE",
    scoring.BAND_NEW: "NEW OUTREACH",
    scoring.BAND_MANUAL: "MANUAL REVIEW",
}


def get_demo_today() -> date:
    return data_store.get_demo_today()


# --- action assembly ---------------------------------------------------------
def _reply_channel(lead) -> str:
    ch = lead.get("conversation_channel") or lead.get("primary_channel")
    return ch if ch in ("dm", "email", "call") else "dm"


def _assemble_actions(lead, demo_today: date) -> list[dict]:
    """The stacked action lines shown on a lead's card, in display order."""
    band = lead.get("band")
    if band == scoring.BAND_MANUAL:
        return [{"key": "review", "label": "needs enrichment before it can be worked",
                 "channel": "review", "is_message": False, "button": "Done"}]

    actions: list[dict] = []
    if band == scoring.BAND_REPLY:
        ch = _reply_channel(lead)
        is_msg = ch in ("dm", "email")
        label = {"dm": "DM reply", "email": "Email reply", "call": "Call back"}[ch]
        actions.append({
            "key": f"reply:{ch}", "label": label, "channel": ch,
            "is_message": is_msg, "is_reply": True, "button": "Sent" if is_msg else "Done",
        })
        if bool(lead.get("warm_call_due")):
            actions.append({"key": "warm:0", "label": "Warm call", "channel": "warm",
                            "is_message": False, "button": "Done"})
        return actions

    # follow_ups_due / new_outreach: scheduled work across parallel tracks.
    for a in engine.due_actions(lead, demo_today):
        ch = a["channel"]
        is_msg = ch in ("dm", "email")
        actions.append({
            "key": f"{ch}:{a['step']}" if ch != "warm" else "warm:0",
            "label": a["label"], "channel": ch, "is_message": is_msg,
            "is_reply": False, "button": "Sent" if is_msg else "Done",
            "due_date": a.get("due_date"),
        })
    return actions


def _compute_actions(df: pd.DataFrame, demo_today: date) -> dict:
    """Return {lead_id: [actions]} for active bands and flag DM-consuming leads."""
    df["_dm_action_due"] = False
    actions_by_lead: dict = {}
    for idx in df.index:
        band = df.at[idx, "band"]
        if band not in BAND_LABELS:
            continue
        acts = _assemble_actions(df.loc[idx], demo_today)
        actions_by_lead[str(df.at[idx, "lead_id"])] = acts
        if any(a["channel"] == "dm" and a["is_message"] for a in acts):
            df.at[idx, "_dm_action_due"] = True
    return actions_by_lead


# --- state for the UI --------------------------------------------------------
def build_state(redraft: bool = True) -> dict:
    """Assemble the full view model the UI renders."""
    demo_today = get_demo_today()
    df = data_store.load()
    state = accounts.load_accounts()
    state = accounts.roll_day_if_needed(state, demo_today.isoformat())

    df = scoring.compute_scores(df, demo_today)
    df = accounts.assign_dm_accounts(df, state)
    actions_by_lead = _compute_actions(df, demo_today)
    cap = accounts.apply_capacity(df, state)

    # Draft messages for the leads we will actually show (today's actioned leads).
    shown_ids = _visible_ids(df, actions_by_lead)
    if redraft:
        api_key = config.openai_api_key()
        shown_actions = {lid: actions_by_lead[lid] for lid in shown_ids
                         if lid in actions_by_lead}
        try:
            df = drafting.ensure_drafts(df, shown_actions, api_key)
        except Exception as exc:  # drafting must never crash the board
            log.warning("drafting failed: %s", exc)
    # Persist assignment + drafts (atomic). Derived/score columns are recomputed
    # every build, so drop the transient ones before writing.
    data_store.save(df.drop(columns=[c for c in ("_dm_action_due",) if c in df.columns]))

    bands = _build_bands(df, actions_by_lead, cap)
    board = _build_board(df, cap, state, demo_today)
    return {
        "demo_today": demo_today,
        "bands": bands,
        "board": board,
        "accounts_state": state,
        "blocked_followups": accounts.blocked_followups(df, state),
    }


def _visible_ids(df: pd.DataFrame, actions_by_lead: dict) -> set:
    ids = set()
    for band in BAND_LABELS:
        sub = scoring.order_band(df[df["band"] == band]).head(VISIBLE_CAP)
        ids.update(str(x) for x in sub["lead_id"])
    return ids


def _card_title(lead) -> str:
    if str(lead.get("store_clean", "")).strip():
        city = str(lead.get("city_clean", "")).strip()
        return f"{lead['store_clean']} — {city}" if city else str(lead["store_clean"])
    h = str(lead.get("handle_clean", "")).strip()
    return f"@{h}" if h else str(lead.get("lead_id", "lead"))


def _build_bands(df: pd.DataFrame, actions_by_lead: dict, cap) -> list[dict]:
    out = []
    for band in BAND_LABELS:
        sub = scoring.order_band(df[df["band"] == band])
        total = len(sub)
        shown = sub.head(VISIBLE_CAP)
        cards = []
        for _, lead in shown.iterrows():
            lid = str(lead["lead_id"])
            acts = []
            for a in actions_by_lead.get(lid, []):
                a = dict(a)
                if a["channel"] == "dm" and a["is_message"]:
                    a["sendable"] = lid in cap.sendable_ids
                    a["rolled"] = lid in cap.rolled_ids
                    a["account"] = str(lead.get("assigned_account", ""))
                else:
                    a["sendable"] = True
                    a["rolled"] = False
                if a["is_message"] or a.get("is_reply"):
                    a["text"] = drafting.get_draft(lead, a)
                elif a["channel"] in ("call", "warm"):
                    a["text"] = drafting.template_message(lead, a)  # prep note
                acts.append(a)
            cards.append({
                "lead_id": lid,
                "title": _card_title(lead),
                "band": band,
                "why": lead.get("why", "—"),
                "score": float(lead.get("score", 0.0)),
                "lead_type": lead.get("lead_type"),
                "account": str(lead.get("assigned_account", "")),
                "actions": acts,
                "can_undo": bool(str(lead.get("undo_snapshot", "")).strip()),
            })
        out.append({"band": band, "label": BAND_LABELS[band], "total": total,
                    "shown": len(cards), "cards": cards})
    return out


def _build_board(df: pd.DataFrame, cap, state, demo_today: date) -> dict:
    counts = {b: int((df["band"] == b).sum()) for b in
              [scoring.BAND_REPLY, scoring.BAND_FOLLOWUP, scoring.BAND_NEW,
               scoring.BAND_MANUAL, scoring.BAND_CLOSED]}
    # This-week commercial snapshot.
    active = df[df["band"].isin([scoring.BAND_REPLY, scoring.BAND_FOLLOWUP, scoring.BAND_NEW])]
    visits = active[(active["sales_goal"] == "book_visit")]
    visit_cities = [c for c in visits["city_clean"].tolist() if str(c).strip()]
    top_cities = [c for c, _ in pd.Series(visit_cities).value_counts().head(3).items()]
    pipeline_value = pd.to_numeric(active["spend_clean"], errors="coerce").fillna(0).sum()

    per_account = list(cap.per_account.values())
    total_sent = sum(a["sent"] for a in per_account)
    total_cap = sum(a["cap"] for a in per_account if a["status"] != accounts.PAUSED)
    return {
        "total_leads": int(len(df)),
        "counts": counts,
        "per_account": per_account,
        "dm_total_sent": total_sent,
        "dm_total_cap": total_cap,
        "visits_to_book": int(len(visits)),
        "visit_cities": top_cities,
        "pipeline_value": float(pipeline_value),
        "demo_today": demo_today,
    }


# --- mutations ---------------------------------------------------------------
_UNDO_FIELDS = ["stage", "num_touches", "last_touch", "last_inbound_text",
                "human_owner_status", "dm_step", "email_step", "call_step",
                "dm_next_date", "email_next_date", "call_next_date",
                "warm_call_due", "reply_sentiment", "notes", "conversation_channel",
                "snoozed_until", "data_quality_flags"]


def _json_safe(v):
    """Coerce pandas/numpy scalars to native Python for JSON snapshots."""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    if hasattr(v, "item"):          # numpy int64 / float64 / bool_
        return v.item()
    if isinstance(v, float) and pd.isna(v):
        return None
    return v


def _snapshot(lead) -> dict:
    return {f: _json_safe(lead.get(f)) for f in _UNDO_FIELDS}


def _find_idx(df: pd.DataFrame, lead_id: str):
    matches = df.index[df["lead_id"].astype(str) == str(lead_id)]
    return matches[0] if len(matches) else None


def mark_sent(lead_id: str, action_key: str) -> None:
    """Advance one track step / dismiss one action. Idempotent (I2): a double
    fire or a second tap on an already-advanced step is a no-op."""
    demo_today = get_demo_today()
    df = data_store.load()
    idx = _find_idx(df, lead_id)
    if idx is None:
        return
    lead = df.loc[idx].copy()

    undo_meta = {"snap": _snapshot(lead), "dm_account": None}
    changed = False

    if action_key.startswith("reply:"):
        if str(lead.get("last_inbound_text", "")).strip():  # else already handled
            ch = action_key.split(":", 1)[1]
            if ch == "dm" and _consume_dm_slot(lead):
                undo_meta["dm_account"] = str(lead.get("assigned_account", ""))
            engine.clear_reply_needed(lead, demo_today)
            changed = True
    elif action_key == "warm:0":
        if bool(lead.get("warm_call_due")):
            engine.advance_track(lead, "warm", demo_today)
            changed = True
    elif action_key == "review":
        lead["snoozed_until"] = demo_today + timedelta(days=1)
        changed = True
    elif ":" in action_key:
        ch, step_s = action_key.split(":", 1)
        if ch in ("dm", "email", "call") and int(lead.get(f"{ch}_step", 0)) == int(step_s):
            if ch == "dm" and _consume_dm_slot(lead):
                undo_meta["dm_account"] = str(lead.get("assigned_account", ""))
            engine.advance_track(lead, ch, demo_today)
            changed = True

    if not changed:
        log.info("mark_sent no-op (idempotent) lead=%s action=%s", lead_id, action_key)
        return

    lead["undo_snapshot"] = json.dumps(undo_meta)
    for col in lead.index:
        df.at[idx, col] = lead[col]
    data_store.save(df)
    log.info("mark_sent lead=%s action=%s -> stage=%s touches=%s",
             lead_id, action_key, lead.get("stage"), lead.get("num_touches"))


def _consume_dm_slot(lead) -> bool:
    """Record a DM send against the lead's account (over-cap sends counted too, F)."""
    acc_id = str(lead.get("assigned_account", "")).strip()
    if not acc_id:
        return False
    state = accounts.load_accounts()
    accounts.record_dm_sent(state, acc_id)
    return True


def save_reply(lead_id: str, inbound_text: str, outcome: str,
               channel: Optional[str] = None) -> None:
    """Apply a reply outcome. Bookings close every track (E1); a positive reply on
    a closed/lost lead is flagged for review, never auto-reactivated (C3)."""
    demo_today = get_demo_today()
    df = data_store.load()
    idx = _find_idx(df, lead_id)
    if idx is None:
        return
    lead = df.loc[idx].copy()
    undo_meta = {"snap": _snapshot(lead), "dm_account": None}

    closed_now = lead.get("stage") in config.CLOSED_STAGES or \
        lead.get("human_owner_status") == "closed"
    if closed_now and outcome == config.OUTCOME_KEEP:
        # C3 — don't resurrect a closed lead automatically; send it to a human.
        flags = list(lead.get("data_quality_flags", []) or [])
        if "lost_reactivation_review" not in flags:
            flags.append("lost_reactivation_review")
        lead["data_quality_flags"] = flags
        lead["human_owner_status"] = "manager_review"
        if inbound_text:
            lead["last_inbound_text"] = inbound_text
    else:
        ch = channel or _reply_channel(lead)
        engine.apply_outcome(lead, outcome, ch, demo_today, inbound_text)

    lead["undo_snapshot"] = json.dumps(undo_meta)
    for col in lead.index:
        df.at[idx, col] = lead[col]
    data_store.save(df)
    log.info("save_reply lead=%s outcome=%s -> stage=%s", lead_id, outcome, lead.get("stage"))


def skip(lead_id: str, action_key: str) -> None:
    """Defer one action to tomorrow (small, secondary)."""
    demo_today = get_demo_today()
    df = data_store.load()
    idx = _find_idx(df, lead_id)
    if idx is None:
        return
    lead = df.loc[idx].copy()
    if ":" in action_key and action_key.split(":", 1)[0] in ("dm", "email", "call"):
        ch = action_key.split(":", 1)[0]
        lead[f"{ch}_next_date"] = demo_today + timedelta(days=1)
    else:
        lead["snoozed_until"] = demo_today + timedelta(days=1)
    for col in lead.index:
        df.at[idx, col] = lead[col]
    data_store.save(df)


def undo_last(lead_id: str) -> None:
    """H2 — per-lead undo of the last Sent/Reply action."""
    df = data_store.load()
    idx = _find_idx(df, lead_id)
    if idx is None:
        return
    raw = str(df.at[idx, "undo_snapshot"]) if "undo_snapshot" in df.columns else ""
    if not raw.strip():
        return
    meta = json.loads(raw)
    snap = meta.get("snap", {})
    for f, v in snap.items():
        if f in data_store.DATE_COLS:
            v = data_store.cleaning.parse_date(v) if v else None
        df.at[idx, f] = v
    if meta.get("dm_account"):  # give the DM slot back
        state = accounts.load_accounts()
        for acc in state["accounts"]:
            if acc["id"] == meta["dm_account"]:
                acc["sent_today"] = max(0, int(acc.get("sent_today", 0)) - 1)
        accounts.save_accounts(state)
    df.at[idx, "undo_snapshot"] = ""
    data_store.save(df)
    log.info("undo_last lead=%s", lead_id)


# --- upload (Section 8) ------------------------------------------------------
def upload_batch(raw: pd.DataFrame) -> dict:
    """Within-batch dedupe → merge against live → re-rank remaining slots.

    Atomic: the merge is computed in memory and only the validated result is
    written (I1). Uploading the same file twice changes nothing (idempotent).
    Returns a short diff for the UI.
    """
    # I4: empty / header-only file is a success, not an error.
    if raw is None or len(raw) == 0:
        return {"added": 0, "skipped": 0, "merged": 0, "within_batch": 0,
                "headline": "0 rows — nothing to add.", "added_examples": []}
    if "lead_id" not in raw.columns or "stage" not in raw.columns:
        raise ValueError("Upload is missing required columns: lead_id and/or stage.")

    existing = data_store.load()
    batch = data_store.clean_external(raw)
    within = batch.attrs.get("within_batch_collapsed", 0)

    # I3 — sanity check overlap so an obviously-wrong file is caught.
    existing_keys = set(existing["dedupe_key"]) if "dedupe_key" in existing else set()
    batch_keys = set(batch["dedupe_key"])
    overlap = len(existing_keys & batch_keys)

    merged, stats = dedup.merge_batch(existing, batch)
    # New leads need band/score context on next build; classification/tracks are
    # already set by clean_external. Persist atomically.
    data_store.save(merged)

    examples = _rerank_examples(merged, stats)
    head = (f"{stats.added} added · {stats.skipped} skipped (already in pipeline) · "
            f"{stats.merged} merged")
    if within:
        head += f" · {within} within-batch dupes collapsed"
    log.info("upload: %s (overlap=%d)", head, overlap)
    return {
        "added": stats.added, "skipped": stats.skipped, "merged": stats.merged,
        "within_batch": within, "overlap_pct": round(100 * overlap / max(1, len(batch_keys))),
        "headline": head, "added_examples": examples,
        "skipped_ids": stats.skipped_ids,
    }


def _rerank_examples(merged: pd.DataFrame, stats) -> list[str]:
    """A couple of '↑ @x → new outreach #1' lines for the upload diff."""
    if not stats.added_keys:
        return []
    demo_today = get_demo_today()
    scored = scoring.compute_scores(merged, demo_today)
    new_rows = scored[scored["dedupe_key"].isin(stats.added_keys)]
    out = []
    for band in (scoring.BAND_NEW, scoring.BAND_REPLY):
        sub = scoring.order_band(scored[scored["band"] == band])
        sub = sub[sub["dedupe_key"].isin(stats.added_keys)]
        for rank, (_, r) in enumerate(sub.head(2).iterrows(), 1):
            name = (str(r["handle_clean"]) and f"@{r['handle_clean']}") or r["store_clean"]
            out.append(f"↑ {name} → {scoring.BAND_REPLY if band==scoring.BAND_REPLY else 'new outreach'} #{rank}")
    return out[:3]


def reset_pipeline() -> None:
    data_store.reset()
    state = accounts.load_accounts()       # fresh demo → zero today's send counters
    for acc in state["accounts"]:
        acc["sent_today"] = 0
    accounts.save_accounts(state)
    log.info("pipeline reset to clean original")


def export_path() -> str:
    return data_store.export_path()


# --- accounts (secondary layer) ----------------------------------------------
def account_summary() -> dict:
    return accounts.load_accounts()


def set_dm_cap(cap: int) -> None:
    state = accounts.load_accounts()
    for acc in state["accounts"]:
        acc["cap"] = int(cap)
    accounts.save_accounts(state)


def add_account(name: Optional[str] = None) -> None:
    state = accounts.load_accounts()
    name = name or f"Account {len(state['accounts']) + 1}"
    state["accounts"].append({"id": name, "status": accounts.ACTIVE,
                              "cap": config.DM_DAILY_LIMIT, "sent_today": 0})
    accounts.save_accounts(state)


def set_account_status(account_id: str, status: str) -> None:
    state = accounts.load_accounts()
    for acc in state["accounts"]:
        if acc["id"] == account_id:
            acc["status"] = status
    accounts.save_accounts(state)


def delete_account(account_id: str) -> tuple[bool, int]:
    """F5 — refuse to delete an account that still owns live conversations."""
    demo_today = get_demo_today()
    df = scoring.compute_scores(data_store.load(), demo_today)
    df = accounts.assign_dm_accounts(df, accounts.load_accounts())
    ok, n = accounts.can_delete_account(df, account_id)
    if not ok:
        return False, n
    state = accounts.load_accounts()
    state["accounts"] = [a for a in state["accounts"] if a["id"] != account_id]
    accounts.save_accounts(state)
    return True, 0
