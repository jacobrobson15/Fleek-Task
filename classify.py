"""
classify.py — edge-case register section C (C1–C3) plus track eligibility.

Lead type is kept strictly separate from contact channel (the brief's central
distinction): a Depop reseller with an email is still a reseller chasing a CALL,
just contacted by email. A shop with only a handle is still a shop chasing a
VISIT, first touched by DM.

Adds: lead_type, primary_channel, conversation_channel, sales_goal,
human_owner_status, and the four track-eligibility booleans the engine consumes.
"""

from __future__ import annotations

import pandas as pd

import config


def _truthy(v: object) -> bool:
    return bool(str(v).strip()) and str(v).strip().lower() != "nan"


def classify_row(row: pd.Series) -> dict:
    """Return the classification columns for a single cleaned lead row."""
    flags = list(row.get("data_quality_flags", []) or [])

    has_store = _truthy(row.get("store_clean"))
    has_handle = _truthy(row.get("handle_clean"))
    has_email = bool(row.get("email_valid"))
    has_phone = bool(row.get("phone_valid"))
    source = str(row.get("source", "")).strip().lower()
    stage = row.get("stage", "new")

    # --- lead_type ----------------------------------------------------------
    if has_store:
        lead_type = "shop"
        if has_handle and "handle_on_store" not in flags:
            flags.append("handle_on_store")          # B5 overlap, keep clearer goal
    elif has_handle:
        lead_type = "reseller"
    elif source in config.SHOP_SOURCES:
        lead_type = "shop"
    elif source in config.RESELLER_SOURCES:
        lead_type = "reseller"
    else:
        lead_type = "unknown"

    sales_goal = "book_visit" if lead_type == "shop" else "book_call"

    # --- track eligibility --------------------------------------------------
    # Email runs for anyone with a valid email (shops AND resellers — Section 3).
    email_active = has_email
    # Cold-call track is shops only; resellers are never cold-called (Section 3).
    call_active = has_phone and lead_type == "shop"
    # DM track: resellers with a handle; or a shop whose ONLY route in is a handle.
    if lead_type == "reseller":
        dm_active = has_handle
    elif lead_type == "shop":
        dm_active = has_handle and not has_email
    else:
        dm_active = has_handle
    # A warm call can fire on a positive reply whenever any phone exists.
    warm_call_eligible = has_phone

    # --- primary_channel (the headline route) -------------------------------
    if lead_type == "reseller":
        if dm_active:
            primary_channel = "dm"
        elif email_active:
            primary_channel = "email"
        elif has_phone:
            primary_channel = "call"            # C1: phone-only reseller
        else:
            primary_channel = "no_contact"
    else:  # shop / unknown
        if email_active:
            primary_channel = "email"
        elif call_active or has_phone:
            primary_channel = "call"
        elif dm_active or has_handle:
            primary_channel = "dm"
        else:
            primary_channel = "no_contact"

    # --- human_owner_status + manual-review reasons -------------------------
    human_owner_status = "active"
    if stage in config.CLOSED_STAGES:
        human_owner_status = "closed"
    else:
        # C2: nothing to contact on at all → needs enrichment (NOT a silent skip).
        if primary_channel == "no_contact":
            human_owner_status = "manager_review"
            if "needs_enrichment" not in flags:
                flags.append("needs_enrichment")
        # C1: a reseller we can only phone — callable, but resellers aren't
        # cold-called, so a human must decide → manual review.
        elif lead_type == "reseller" and primary_channel == "call":
            human_owner_status = "manager_review"
            if "reseller_phone_only" not in flags:
                flags.append("reseller_phone_only")
        # Any blocking data-quality flag that means we can't act as-is.
        elif {"email_unparseable", "phone_unusable"} & set(flags) and \
                primary_channel == "no_contact":
            human_owner_status = "manager_review"

    # --- conversation_channel ----------------------------------------------
    # Locks to where they actually engage; for an already-engaged lead we seed it
    # with the primary channel (refined whenever a reply is saved, E3).
    conversation_channel = ""
    if stage in config.CONTACTED_STAGES and _truthy(row.get("last_inbound_text")):
        conversation_channel = primary_channel

    return {
        "lead_type": lead_type,
        "primary_channel": primary_channel,
        "conversation_channel": conversation_channel,
        "sales_goal": sales_goal,
        "human_owner_status": human_owner_status,
        "dm_active": dm_active,
        "email_active": email_active,
        "call_active": call_active,
        "warm_call_eligible": warm_call_eligible,
        "data_quality_flags": flags,
    }


def classify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Apply classification to every row, returning an enriched frame."""
    df = df.copy()
    enriched = df.apply(lambda r: pd.Series(classify_row(r)), axis=1)
    for col in enriched.columns:
        df[col] = enriched[col]
    return df
