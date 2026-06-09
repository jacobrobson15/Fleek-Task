"""
app.py — the Streamlit layer. A thin list-view UI over logic.py (no brain here).

Two surfaces:
  * Today   — the progress board (read-only) + the four-band activity list, the
              only interactive part. One card per lead, parallel actions stacked.
  * Accounts — the secondary capacity layer + its guards.

The rep's whole job: read the Why, copy the message, send it, hit Sent. If they
reply, hit Reply and say what they said. ~30 seconds to train.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import accounts as accounts_mod
import config
import logic
import scoring

st.set_page_config(page_title="Fleek Pipeline", page_icon="🧥", layout="wide")

BAND_COLORS = {
    scoring.BAND_REPLY: "#E8463B",
    scoring.BAND_FOLLOWUP: "#E08A1E",
    scoring.BAND_NEW: "#2E8B57",
    scoring.BAND_MANUAL: "#6B7280",
}
OUTCOME_OPTIONS = {
    "keep talking": config.OUTCOME_KEEP,
    "call booked": config.OUTCOME_CALL_BOOKED,
    "visit booked": config.OUTCOME_VISIT_BOOKED,
    "won": config.OUTCOME_WON,
    "lost": config.OUTCOME_LOST,
}


def money(v: float) -> str:
    if v >= 1000:
        return f"£{v/1000:.0f}k"
    return f"£{v:.0f}"


# --- progress board ----------------------------------------------------------
def render_board(board: dict, blocked: int):
    left, right = st.columns([1, 1.2])
    with left:
        st.markdown("#### PIPELINE")
        st.markdown(f"**{board['total_leads']} leads**")
        c = board["counts"]
        rows = [
            ("Reply needed", c[scoring.BAND_REPLY], BAND_COLORS[scoring.BAND_REPLY]),
            ("Follow-ups due", c[scoring.BAND_FOLLOWUP], BAND_COLORS[scoring.BAND_FOLLOWUP]),
            ("New outreach", c[scoring.BAND_NEW], BAND_COLORS[scoring.BAND_NEW]),
            ("Manual review", c[scoring.BAND_MANUAL], BAND_COLORS[scoring.BAND_MANUAL]),
            ("Closed / owned", c[scoring.BAND_CLOSED], "#9CA3AF"),
        ]
        for label, n, color in rows:
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"border-left:3px solid {color};padding:2px 8px;margin:2px 0;'>"
                f"<span>{label}</span><b>{n}</b></div>",
                unsafe_allow_html=True,
            )
    with right:
        st.markdown("#### TODAY — DMs")
        for a in board["per_account"]:
            if a["status"] == accounts_mod.PAUSED:
                st.markdown(f"`{a['id']}`  —  **paused**")
            else:
                tag = "  · follow-ups only" if a["status"] == accounts_mod.FOLLOWUPS_ONLY else ""
                over = f"  ⚠️ {a['over']} over cap" if a["over"] else ""
                st.markdown(
                    f"`{a['id']}`  **{a['sent']} / {a['cap']} sent**  ·  {a['left']} left{tag}{over}")
        st.markdown(
            f"<div style='border-top:1px solid #ddd;margin-top:4px;padding-top:4px;'>"
            f"<b>Total {board['dm_total_sent']} / {board['dm_total_cap']} capacity</b></div>",
            unsafe_allow_html=True)
        st.markdown("#### THIS WEEK")
        cities = ", ".join(board["visit_cities"]) if board["visit_cities"] else "—"
        st.markdown(f"{board['visits_to_book']} visits to book  ({cities})")
        st.markdown(f"**{money(board['pipeline_value'])} pipeline in play**")
    if blocked:
        st.warning(f"⛔ {blocked} follow-ups blocked — reactivate an account in the Accounts tab.")


# --- a single action line ----------------------------------------------------
def render_action(lead_id: str, action: dict):
    label = action["label"]
    chip = ""
    if action["channel"] == "dm" and action.get("is_message"):
        if action.get("rolled") and not action.get("sendable"):
            chip = " · rolls to tomorrow (cap reached)"
        elif action.get("account"):
            chip = f" · from {action['account']}"
    st.markdown(f"**{label}**{chip}")

    text = action.get("text", "")
    if action.get("is_message") or action.get("is_reply"):
        st.code(text, language=None)  # the copy icon IS the Copy button
    else:
        st.caption(text)  # call prep / review note

    btn_label = action.get("button", "Sent")
    disabled = action["channel"] == "dm" and action.get("is_message") \
        and action.get("rolled") and not action.get("sendable")
    if st.button(btn_label, key=f"{lead_id}|{action['key']}|done", disabled=disabled,
                 use_container_width=False):
        logic.mark_sent(lead_id, action["key"])
        st.rerun()


# --- a single card -----------------------------------------------------------
def render_card(card: dict):
    color = BAND_COLORS.get(card["band"], "#888")
    type_chip = "🛍️ shop" if card["lead_type"] == "shop" else "📱 reseller"
    with st.container(border=True):
        head = f"### {card['title']}"
        st.markdown(head)
        st.caption(f"{type_chip}  ·  score {card['score']:.2f}")
        st.markdown(
            f"<span style='color:{color};'><b>Why</b></span>  {card['why']}",
            unsafe_allow_html=True)
        st.markdown("**Do**")
        for action in card["actions"]:
            render_action(card["lead_id"], action)

        cols = st.columns([1, 1, 4])
        with cols[0]:
            with st.popover("Reply"):
                render_reply_form(card["lead_id"])
        with cols[1]:
            if card.get("can_undo"):
                if st.button("Undo", key=f"{card['lead_id']}|undo"):
                    logic.undo_last(card["lead_id"])
                    st.rerun()
        with cols[2]:
            if st.button("Skip", key=f"{card['lead_id']}|skip", type="tertiary"):
                first_key = card["actions"][0]["key"] if card["actions"] else ""
                logic.skip(card["lead_id"], first_key)
                st.rerun()


def render_reply_form(lead_id: str):
    st.markdown("**Paste what they said:**")
    text = st.text_area("inbound", key=f"{lead_id}|reply_text", label_visibility="collapsed")
    choice = st.radio("Outcome", list(OUTCOME_OPTIONS.keys()),
                      key=f"{lead_id}|reply_outcome", horizontal=False)
    outcome = OUTCOME_OPTIONS[choice]
    needs_confirm = outcome in (config.OUTCOME_WON, config.OUTCOME_LOST)
    confirmed = True
    if needs_confirm:
        confirmed = st.checkbox(f"Confirm — mark this lead **{choice}**",
                                key=f"{lead_id}|reply_confirm")
    if outcome in config.BOOKING_OUTCOMES:
        st.info("This closes every track on the lead — no further messages go out.")
    if st.button("Save", key=f"{lead_id}|reply_save", disabled=not confirmed):
        logic.save_reply(lead_id, text.strip(), outcome)
        st.rerun()


# --- the four-band list ------------------------------------------------------
def render_list(bands: list[dict]):
    any_cards = False
    for band in bands:
        if band["total"] == 0:
            continue
        any_cards = True
        st.markdown(f"### {band['label']}  ·  {band['total']}")
        if band["shown"] < band["total"]:
            st.caption(f"{band['total']} due — showing top {band['shown']} by score.")
        for card in band["cards"]:
            render_card(card)
        st.markdown("")
    if not any_cards:
        st.success("Inbox zero — nothing due today. Upload a new drop or come back tomorrow.")


# --- accounts tab ------------------------------------------------------------
def render_accounts_tab(state: dict):
    st.markdown("### Accounts — DM capacity layer")
    st.caption("Secondary by design: the demo runs on one account at a 40 cap. The same "
               "logic scales to N accounts; follow-ups always stay with their original sender.")

    cur_cap = state["accounts"][0]["cap"] if state["accounts"] else config.DM_DAILY_LIMIT
    new_cap = st.number_input("DM daily limit (per account)", min_value=1, max_value=500,
                              value=int(cur_cap), step=5,
                              help="The one setting the brief calls out as legitimately changeable.")
    if new_cap != cur_cap:
        if new_cap < cur_cap:
            st.warning("Lowering the cap mid-day. In-flight conversations are never reassigned; "
                       "remaining slots are clamped to ≥ 0 and any overage is surfaced honestly.")
        if st.button("Apply new cap"):
            logic.set_dm_cap(int(new_cap))
            st.rerun()

    st.divider()
    for acc in state["accounts"]:
        c1, c2, c3 = st.columns([2, 2, 3])
        with c1:
            st.markdown(f"**{acc['id']}**")
            st.caption(f"{acc['sent_today']} sent today · cap {acc['cap']}")
        with c2:
            options = [accounts_mod.ACTIVE, accounts_mod.FOLLOWUPS_ONLY, accounts_mod.PAUSED]
            idx = options.index(acc["status"]) if acc["status"] in options else 0
            new_status = st.selectbox("status", options, index=idx,
                                      key=f"status|{acc['id']}", label_visibility="collapsed")
            if new_status != acc["status"]:
                logic.set_account_status(acc["id"], new_status)
                st.rerun()
        with c3:
            if st.button("Delete", key=f"del|{acc['id']}"):
                ok, n = logic.delete_account(acc["id"])
                if not ok:
                    st.error(f"Can't delete — {n} live conversations are assigned here. "
                             f"Reassign or archive them first.")
                else:
                    st.rerun()
    if st.button("➕ Add account"):
        logic.add_account()
        st.rerun()


# --- sidebar (data ops) ------------------------------------------------------
def render_sidebar():
    st.sidebar.markdown("## 🧥 Fleek Pipeline")
    st.sidebar.caption(f"Demo day: **{logic.get_demo_today():%d %b %Y}**")
    st.sidebar.markdown("### New drop")
    up = st.sidebar.file_uploader("Upload a CSV (e.g. new_drop_day2.csv)", type=["csv"])
    if up is not None and not st.session_state.get(f"uploaded_{up.name}_{up.size}"):
        try:
            raw = pd.read_csv(up, dtype=str)
            diff = logic.upload_batch(raw)
            st.session_state[f"uploaded_{up.name}_{up.size}"] = True
            st.session_state["last_diff"] = diff
            st.rerun()
        except ValueError as exc:
            st.sidebar.error(str(exc))
        except Exception as exc:  # pragma: no cover
            st.sidebar.error(f"Couldn't read that file: {exc}")

    if st.session_state.get("last_diff"):
        d = st.session_state["last_diff"]
        st.sidebar.success(d["headline"])
        for line in d.get("added_examples", []):
            st.sidebar.caption(line)
        if d.get("overlap_pct", 100) < 20 and d["added"] > 0:
            st.sidebar.warning(f"Heads up: shares only {d['overlap_pct']}% of keys with the "
                               "pipeline — is this the right file?")

    st.sidebar.divider()
    st.sidebar.markdown("### Data")
    with open(logic.export_path(), "rb") as fh:
        st.sidebar.download_button("⬇️ Export full CSV", fh, file_name="fleek_pipeline_export.csv",
                                   mime="text/csv", use_container_width=True)
    if st.sidebar.button("↩️ Reset to clean original", use_container_width=True):
        logic.reset_pipeline()
        st.session_state.pop("last_diff", None)
        for k in list(st.session_state.keys()):
            if str(k).startswith("uploaded_"):
                st.session_state.pop(k)
        st.rerun()

    if not config.openai_api_key():
        st.sidebar.info("No OPENAI_API_KEY set — using built-in message templates. "
                        "Set the key (env var or Streamlit secret) for LLM drafting.")


# --- main --------------------------------------------------------------------
def main():
    render_sidebar()
    state = logic.build_state(redraft=True)

    st.title("Today")
    render_board(state["board"], state["blocked_followups"])
    st.divider()

    tab_today, tab_accounts = st.tabs(["Activity list", "Accounts"])
    with tab_today:
        render_list(state["bands"])
    with tab_accounts:
        render_accounts_tab(state["accounts_state"])


if __name__ == "__main__":
    main()
