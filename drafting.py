"""
drafting.py — message drafting (build plan step 6).

The LLM (OpenAI gpt-4o-mini) is called ONLY for leads actioned today, and only
for message channels (DM / Email / a warm-call opener). Every draft is keyed by a
content hash: if the lead's relevant state hasn't changed, the stored draft is
reused and the model is never called again. That is what makes the brief's
"doesn't repeat itself" PROVABLE — the same draft is never regenerated or re-sent.

If no API key is configured the tool degrades gracefully to channel/step-aware
templates, so the demo always works. Drafting never invents a reply to a blank
inbound (no hallucinated quotes).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional

import pandas as pd

import config


# --- identity / hashing ------------------------------------------------------
def action_key(action: dict) -> str:
    """Stable per-action slot key, e.g. 'dm:0', 'email:2', 'reply:dm', 'warm:0'."""
    if action.get("is_reply"):
        return f"reply:{action.get('channel', 'dm')}"
    return f"{action['channel']}:{action.get('step', 0)}"


def _first_name(lead) -> str:
    name = str(lead.get("contact_name", "")).strip()
    if name:
        return name.split()[0]
    return ""


def content_hash(lead, action: dict) -> str:
    """Hash of everything that should change the draft. Same hash → same draft."""
    parts = [
        str(lead.get("lead_id", "")),
        action_key(action),
        str(lead.get("sales_goal", "")),
        _first_name(lead),
        str(lead.get("store_clean", "")),
        str(lead.get("handle_clean", "")),
        str(lead.get("lead_type", "")),
        config.BOOKING_LINK,
    ]
    if action.get("is_reply"):
        parts.append(str(lead.get("last_inbound_text", "")))  # their words → drives reply
    raw = "||".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --- templates (fallback + LLM scaffolding) ----------------------------------
def _addressee(lead) -> str:
    fn = _first_name(lead)
    if fn:
        return fn
    if lead.get("lead_type") == "shop" and str(lead.get("store_clean", "")).strip():
        return "there"
    return "there"


def template_message(lead, action: dict) -> str:
    """Deterministic fallback copy — used when no API key, and as LLM guidance."""
    who = _addressee(lead)
    link = config.BOOKING_LINK
    goal_is_visit = lead.get("sales_goal") == "book_visit"
    city = str(lead.get("city_clean", "")).strip()
    channel = action.get("channel")
    step = int(action.get("step", 0))

    if action.get("is_reply"):
        inbound = str(lead.get("last_inbound_text", "")).strip()
        if not inbound:  # never fabricate a reply to nothing
            return _template_outreach(lead, action)
        if goal_is_visit:
            return (f"Hi {who}, great to hear from you. Happy to cover all of that — "
                    f"the quickest way is a 15-min visit so we can show you the bundle "
                    f"list in person. Does this week work? {link}")
        return (f"Hey {who}! Yeah, happy to help with that. Easiest is a quick call where "
                f"I walk you through the bundle list and pricing — grab a slot here: {link}")

    if channel == "warm":
        return (f"Warm call prep — {who}: they're engaged. Reconfirm interest, then book "
                f"the {'visit' if goal_is_visit else 'call'}. Lead with the bundle list.")
    if channel == "call":
        spend = lead.get("spend_clean")
        spend_txt = f"£{int(spend):,}/mo est" if spend and not pd.isna(spend) else "warm prospect"
        when = " · mornings best" if "morning" in str(lead.get("last_inbound_text", "")).lower() else ""
        return f"Cold call prep — {who} ({city}): {spend_txt}{when}. Pitch a visit to show the bundle list."

    return _template_outreach(lead, action, step, channel, who, link, goal_is_visit, city)


def _template_outreach(lead, action, step=0, channel=None, who=None, link=None,
                       goal_is_visit=None, city=None) -> str:
    channel = channel or action.get("channel")
    step = step or int(action.get("step", 0))
    who = who or _addressee(lead)
    link = link or config.BOOKING_LINK
    if goal_is_visit is None:
        goal_is_visit = lead.get("sales_goal") == "book_visit"
    city = city if city is not None else str(lead.get("city_clean", "")).strip()

    if channel == "email":
        ladder = [
            (f"Hi {who}, we're Fleek — a B2B marketplace that buys vintage stock in bulk. "
             f"We're sourcing in {city or 'your area'} and your range looks like a strong fit. "
             f"Could we pop by for a quick visit to show you what we'd take? {link}"),
            (f"Hi {who}, circling back — we'd love to set up a short visit to walk through the "
             f"bundle list and how we pay. Does this week suit? {link}"),
            (f"Hi {who}, one more from me: most shops we visit are surprised how much we take in "
             f"one go. Worth 15 minutes? Happy to come to you. {link}"),
            (f"Hi {who}, I'll leave it here for now — if buying a bundle in one go is ever useful, "
             f"the door's open: {link}. Thanks!"),
        ]
        return ladder[min(step, len(ladder) - 1)]

    # DM ladder (resellers, or shop-by-handle)
    ladder = [
        (f"Hey! Saw you're moving serious volume — we're Fleek, we buy vintage bundles in bulk "
         f"(100+ pieces at a time). Could show you our buy list on a quick call? {link}"),
        (f"Hey {who or ''}, following up — genuinely think we'd take a big chunk of your stock in "
         f"one go. Worth a quick call? {link}".replace("  ", " ")),
        (f"Hey, last couple of nudges from me — happy to send the bundle list ahead of a call so "
         f"you can see numbers first. Keen? {link}"),
        (f"All good if the timing's off! If selling a bundle in one go ever helps, I'm here: {link}"),
    ]
    return ladder[min(step, len(ladder) - 1)]


# --- LLM ---------------------------------------------------------------------
_SYSTEM = (
    "You are an SDR for Fleek, a B2B marketplace that buys secondhand/vintage clothing "
    "in bulk (100+ items at a time) from resellers and physical shops. You write short, "
    "warm, non-spammy outreach. Resellers are reached to book a CALL; shops to book a "
    "VISIT. Never invent facts the prospect didn't say. Output ONLY the message text — "
    "no subject line, no quotes, no preamble. Keep it under 60 words."
)


def _llm_draft(lead, action: dict, api_key: str) -> Optional[str]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        guide = template_message(lead, action)  # gives the model the intent/structure
        ctx = {
            "lead_type": lead.get("lead_type"),
            "goal": lead.get("sales_goal"),
            "channel": action.get("channel"),
            "step_label": action.get("label"),
            "name": _first_name(lead) or None,
            "store": str(lead.get("store_clean", "")).strip() or None,
            "handle": str(lead.get("handle_clean", "")).strip() or None,
            "city": str(lead.get("city_clean", "")).strip() or None,
            "their_message": (str(lead.get("last_inbound_text", "")).strip()
                              if action.get("is_reply") else None),
            "booking_link": config.BOOKING_LINK,
        }
        user = (
            "Write the message for this outreach step.\n"
            f"Context: {json.dumps({k: v for k, v in ctx.items() if v is not None})}\n"
            f"Reference draft (match this intent, improve the wording): {guide}"
        )
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.7,
            max_tokens=140,
        )
        text = resp.choices[0].message.content.strip().strip('"')
        return text or None
    except Exception:  # network / auth / quota — fall back silently
        return None


# --- public API --------------------------------------------------------------
def draft_for_action(lead, action: dict, api_key: Optional[str]) -> str:
    """Return draft text for one action (LLM if possible, else template)."""
    if api_key:
        text = _llm_draft(lead, action, api_key)
        if text:
            return text
    return template_message(lead, action)


def _load_drafts(lead) -> dict:
    raw = lead.get("drafts_json", "")
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def ensure_drafts(df: pd.DataFrame, actions_by_lead: dict, api_key: Optional[str]) -> pd.DataFrame:
    """Draft (or reuse) messages for today's actioned leads only.

    actions_by_lead: {lead_id: [action dicts]} — only the cards shown today.
    Reuses any stored draft whose content hash is unchanged (no re-call, no
    re-send). Message channels only; call actions carry prep text, not a draft.
    """
    df = df.copy()
    for col in ("drafts_json", "draft_message", "last_message_hash", "draft_generated_at"):
        if col not in df.columns:
            df[col] = ""

    id_to_idx = {str(lid): i for i, lid in zip(df.index, df["lead_id"])}
    for lead_id, actions in actions_by_lead.items():
        idx = id_to_idx.get(str(lead_id))
        if idx is None:
            continue
        lead = df.loc[idx]
        drafts = _load_drafts(lead)
        top_hash = top_text = ""
        for action in actions:
            if action.get("channel") in ("call", "warm") and not action.get("is_reply"):
                # Calls show prep text generated on the fly; no LLM, no storage.
                continue
            key = action_key(action)
            h = content_hash(lead, action)
            cached = drafts.get(key)
            if cached and cached.get("hash") == h and cached.get("text"):
                text = cached["text"]                      # reuse — provably no repeat
            else:
                text = draft_for_action(lead, action, api_key)
                drafts[key] = {"hash": h, "text": text,
                               "at": datetime.utcnow().isoformat(timespec="seconds")}
            if not top_hash:
                top_hash, top_text = h, text
        df.at[idx, "drafts_json"] = json.dumps(drafts)
        if top_hash:
            df.at[idx, "draft_message"] = top_text
            df.at[idx, "last_message_hash"] = top_hash
            df.at[idx, "draft_generated_at"] = datetime.utcnow().isoformat(timespec="seconds")
    return df


def get_draft(lead, action: dict) -> str:
    """Read a stored draft for display; fall back to a template if absent."""
    if action.get("channel") in ("call", "warm") and not action.get("is_reply"):
        return template_message(lead, action)  # prep text
    drafts = _load_drafts(lead)
    cached = drafts.get(action_key(action))
    if cached and cached.get("text"):
        return cached["text"]
    return template_message(lead, action)
