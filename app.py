"""
app.py — Streamlit host for the Fleek Daily Run UI.

Serves the Claude Design prototype (frontend/) populated with live pipeline
data from logic.py. No backend logic lives here — it only adapts the
build_state() output into the FLEEK_DATA shape the design expects.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import accounts as accts_mod
import data_store
import logic
import scoring

st.set_page_config(
    page_title="Fleek",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Strip all Streamlit chrome so the design fills the viewport
st.markdown("""<style>
#MainMenu, header[data-testid="stHeader"], footer { display: none !important; }
.block-container { padding: 0 !important; max-width: 100% !important; }
section.main > div:first-child { padding: 0 !important; }
[data-testid="stAppViewContainer"] { padding: 0 !important; }
.stApp { background: #FAFAFA; }
iframe { border: none !important; }
</style>""", unsafe_allow_html=True)

FRONTEND = Path(__file__).parent / "frontend"


def _s(v, default: str = "") -> str:
    """Coerce a possibly-NaN DataFrame value to a clean string."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return str(v).strip() or default


# ── Data adapter ─────────────────────────────────────────────────────────────

def build_fleek_data() -> dict:
    """Convert logic.build_state() + raw DataFrame into the FLEEK_DATA shape."""
    state = logic.build_state(redraft=True)
    df = data_store.load()

    demo_today = state["demo_today"]

    # Raw field lookup keyed by lead_id
    raw: dict = {_s(row["lead_id"]): row for _, row in df.iterrows()}

    # ── Accounts ──────────────────────────────────────────────────────────
    acct_rows = state["accounts_state"]["accounts"]
    acct_by_id = {a["id"]: a.get("handle", a["id"]) for a in acct_rows}

    # Mid-conversation count per account (reply_needed + follow_ups_due)
    mid_convo: dict = {}
    if "assigned_account" in df.columns:
        active_bands = ["reply_needed", "follow_ups_due"]
        for a in acct_rows:
            aid = a["id"]
            mask = (df["assigned_account"].astype(str) == str(aid)) & \
                   (df["band"].isin(active_bands))
            mid_convo[aid] = int(mask.sum())

    accounts_data = []
    for a in acct_rows:
        used = int(a.get("sent_today", 0))
        cap = int(a.get("cap", 40))
        status_raw = a.get("status", accts_mod.ACTIVE)
        if used >= cap:
            status = "At limit"
        elif status_raw == accts_mod.PAUSED:
            status = "Paused"
        elif status_raw == accts_mod.FOLLOWUPS_ONLY:
            status = "Follow-ups only"
        else:
            status = "Active"
        accounts_data.append({
            "id": a["id"],
            "handle": a.get("handle", a["id"]),
            "used": used,
            "cap": cap,
            "status": status,
            "midConvoCount": mid_convo.get(a["id"], 0),
            "inProgress": accts_mod.in_progress_count(df, a["id"]),
            "queued": accts_mod.queued_count(df, a["id"]),
        })

    # ── Classify cards into reseller bands and shop list ──────────────────
    replies_r: list = []
    followups_r: list = []
    newout_r: list = []
    shops_all: list = []
    pos = 1  # queue position across all reseller rows

    for band_data in state["bands"]:
        band = band_data["band"]
        for card in band_data["cards"]:
            lid = card["lead_id"]
            r = raw.get(lid)
            lead_type = card.get("lead_type", "reseller")
            actions = card.get("actions", [])

            # First message action → draft + channel
            draft = ""
            channel = "dm"
            for a in actions:
                if a.get("is_message"):
                    draft = _s(a.get("text", ""))
                    channel = a.get("channel", "dm")
                    break

            # Raw DataFrame fields
            if r is not None:
                handle    = _s(r.get("handle_clean"))
                last_inb  = _s(r.get("last_inbound_text"))
                city      = _s(r.get("city_clean"))
                phone     = _s(r.get("phone_clean"))
                store     = _s(r.get("store_clean")) or card["title"].split(" — ")[0]
            else:
                handle   = card["title"].lstrip("@")
                last_inb = ""
                city     = ""
                phone    = ""
                store    = card["title"].split(" — ")[0]

            if lead_type == "shop":
                # Determine primary track from first email/call action
                track = "email"
                for a in actions:
                    if a.get("channel") in ("email", "call"):
                        track = a["channel"]
                        break

                # Find email and call actions for step/due data
                email_action = next((a for a in actions if a.get("channel") == "email"), None)
                call_action  = next((a for a in actions if a.get("channel") == "call"),  None)

                email_step_n = email_action.get("step", 0) if email_action else (int(r.get("email_step") or 0) if r is not None else 0)
                call_step_n  = call_action.get("step",  0) if call_action  else (int(r.get("call_step")  or 0) if r is not None else 0)

                has_phone_v = bool(phone and phone != "—")

                def _due_label(due_date_val):
                    if not isinstance(due_date_val, date):
                        return ""
                    days = (due_date_val - demo_today).days
                    if days <= 0:   return "due today"
                    if days == 1:   return "due tomorrow"
                    if days < 7:    return f"due {due_date_val.strftime('%A')}"
                    return f"due in {days} days"

                # A channel is "due today" if it's the primary track OR its next_date is today
                email_next_v = r.get("email_next_date") if r is not None else None
                call_next_v  = r.get("call_next_date")  if r is not None else None
                email_due_today = (track == "email") or (
                    isinstance(email_next_v, date) and email_next_v == demo_today)
                call_due_today  = (track == "call") or (has_phone_v and
                    isinstance(call_next_v, date) and call_next_v == demo_today)

                email_due_label = "due today" if email_due_today else _due_label(email_next_v)
                call_due_label  = ("due today" if call_due_today else _due_label(call_next_v)) if has_phone_v else ""

                def _sec_hdr(kind, step, total, due_label):
                    h = f"{kind} · step {step} of {total}" if kind == "Email" else f"{kind} · attempt {step} of {total}"
                    return h + (f" · {due_label}" if due_label else "")

                email_sec_hdr = _sec_hdr("Email", min(email_step_n+1, 4), 4, email_due_label)
                call_sec_hdr  = _sec_hdr("Call", min(call_step_n+1, 2), 2, call_due_label) if (has_phone_v and (call_action or call_step_n > 0 or call_due_today)) else ""

                raw_call_step = int(r.get("call_step") or 0) if r is not None else 0

                due_line = actions[0]["label"] if actions else "Due today"
                other_track = ""
                first_seen = False
                for a in actions:
                    if a.get("channel") in ("email", "call"):
                        if not first_seen:
                            first_seen = True
                        else:
                            other_track = a.get("label", "")
                            break

                shops_all.append({
                    "id": lid,
                    "store": store,
                    "city": city,
                    "phone": phone,
                    "track": track,
                    "band": band,
                    "warm": False,
                    "dueLine": due_line,
                    "draft": draft,
                    "otherTrack": other_track,
                    "emailStep":      email_step_n,
                    "callStep":       call_step_n,
                    "emailDueToday":  email_due_today,
                    "callDueToday":   call_due_today,
                    "emailDueLabel":  email_due_label,
                    "callDueLabel":   call_due_label,
                    "emailSectionHdr": email_sec_hdr,
                    "callSectionHdr":  call_sec_hdr,
                    "callNotAttempted": (raw_call_step == 0),
                    "why": card.get("why", ""),
                })

            else:
                # Reseller — compute quiet secondary-channel context line
                secondary_line = ""
                if r is not None:
                    try:
                        if channel == "email" and bool(r.get("dm_active")):
                            dm_step = int(r.get("dm_step") or 0)
                            acc_id = _s(r.get("assigned_account"))
                            acc_handle = acct_by_id.get(acc_id, "")
                            dm_next = r.get("dm_next_date")
                            parts = [f"DM — step {dm_step} of 4"]
                            if acc_handle:
                                parts.append(f"assigned to @{acc_handle}")
                            if isinstance(dm_next, date) and dm_next is not None:
                                days = (dm_next - demo_today).days
                                if days > 0:
                                    word = "day" if days == 1 else "days"
                                    if band != scoring.BAND_REPLY:
                                        parts.append(f"fallback due in {days} {word} if email goes cold")
                                    else:
                                        parts.append(f"due in {days} {word}")
                                elif days == 0:
                                    parts.append("due today")
                            secondary_line = " · ".join(parts)
                        elif channel == "dm" and bool(r.get("email_active")):
                            email_step = int(r.get("email_step") or 0)
                            email_next = r.get("email_next_date")
                            parts = [f"Email — step {email_step} of 4"]
                            if isinstance(email_next, date) and email_next is not None:
                                days = (email_next - demo_today).days
                                if days > 0:
                                    parts.append(f"due {email_next.strftime('%A')}")
                                elif days == 0:
                                    parts.append("due today")
                            secondary_line = " · ".join(parts)
                    except Exception:
                        secondary_line = ""

                dm_active_v   = bool(r.get("dm_active"))    if r is not None else False
                email_active_v = bool(r.get("email_active")) if r is not None else False
                dm_step_v     = int(r.get("dm_step")    or 0) if r is not None else 0
                email_step_v  = int(r.get("email_step") or 0) if r is not None else 0
                overdue_v     = int(r.get("overdue_days") or 0) if r is not None else 0

                item: dict = {
                    "id": lid,
                    "handle": handle or lid,
                    "name": "",
                    "band": band,
                    "lastInbound": last_inb if last_inb else None,
                    "account": card.get("account", ""),
                    "why": card.get("why", ""),
                    "draft": draft,
                    "secondaryLine": secondary_line,
                    "primaryChannel": channel,
                    "dmActive": dm_active_v,
                    "dmStep": dm_step_v,
                    "emailActive": email_active_v,
                    "emailStep": email_step_v,
                    "overdueDays": overdue_v,
                }

                if band == scoring.BAND_REPLY:
                    item["daysAgo"] = 1
                    replies_r.append(item)
                elif band == scoring.BAND_FOLLOWUP:
                    item["isStall"] = bool(last_inb)
                    followups_r.append(item)
                else:
                    # new_outreach and manual_review both go in the new outreach band
                    newout_r.append(item)

                pos += 1

    # ── Fix queued count to match new-outreach cards visible on screen ───
    newout_by_handle: dict = {}
    for item in newout_r:
        h = item.get("account", "")
        if h:
            newout_by_handle[h] = newout_by_handle.get(h, 0) + 1
    for acc in accounts_data:
        acc["queued"] = newout_by_handle.get(acc["id"], 0)

    # ── City groups for shops ─────────────────────────────────────────────
    city_map: dict = {}
    for shop in shops_all:
        c = shop["city"] or "Other"
        city_map.setdefault(c, []).append(shop)

    cities_data = []
    for city_name, city_shops in city_map.items():
        warm_count = sum(1 for s in city_shops if s.get("warm"))
        cities_data.append({
            "city": city_name,
            "shops": city_shops,
            "warmCount": warm_count,
            "showPrompt": warm_count >= 3,
        })

    # ── Admin stats ───────────────────────────────────────────────────────
    board = state["board"]
    needs_review = board["counts"].get(scoring.BAND_MANUAL, 0)

    return {
        "today": demo_today.strftime("%a %d %b"),
        "accounts": accounts_data,
        "resellers": {
            "doneStart": 0,
            "total": len(replies_r) + len(followups_r) + len(newout_r),
            "replies": replies_r,
            "followups": followups_r,
            "newout": newout_r,
        },
        "shops": {
            "cities": cities_data,
            "doneStart": 0,
            "total": len(shops_all),
        },
        "admin": {
            "fileName": "fleek_pipeline_live.csv",
            "added": 0,
            "updated": 0,
            "merged": 0,
            "needsReview": needs_review,
            "reupload": {"added": 0, "updated": 0, "merged": 0, "needsReview": 0},
            "flags": [],
        },
    }


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(fleek_data: dict) -> str:
    support_js   = (FRONTEND / "support.js").read_text()
    template_html = (FRONTEND / "template.html").read_text()
    logic_js     = (FRONTEND / "logic.js").read_text()

    data_json = json.dumps(fleek_data, ensure_ascii=False, default=str)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>{support_js}</script>
<script>
window.FLEEK_DATA = {data_json};
try {{ window.dispatchEvent(new Event("fleekdata")); }} catch(e) {{}}
</script>
</head>
<body>
<x-dc>
{template_html}
</x-dc>
<script type="text/x-dc" data-dc-script>
{logic_js}
</script>
<script>
// Auto-resize iframe to content height
function notifyHeight() {{
  var h = document.documentElement.scrollHeight;
  window.parent.postMessage({{ isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h }}, "*");
}}
new ResizeObserver(notifyHeight).observe(document.body);
notifyHeight();
</script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    try:
        fleek_data = build_fleek_data()
    except Exception as exc:
        st.error(f"Could not load pipeline data: {exc}")
        import traceback
        st.code(traceback.format_exc())
        return

    html = build_html(fleek_data)
    components.html(html, height=900, scrolling=True)


if __name__ == "__main__":
    main()
