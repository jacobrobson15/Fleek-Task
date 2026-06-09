"""
cleaning.py — edge-case register section A (A1–A10).

Runs before anything else reads the data. Adds cleaned/typed columns and a
``data_quality_flags`` string that later drives the Manual Review band. Cleaning
is pure: it never decides who to contact, only what the data actually says.

The canonical schema after cleaning keeps every original column (so we can show
the rep the raw text) and adds typed ``*_clean`` companions.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Tuple

import pandas as pd

import config

# --- A1: handle canonicalisation ---------------------------------------------
_HANDLE_STRIP = re.compile(r"^(https?://)?(www\.)?(instagram\.com/|instagr\.am/)", re.I)


def canon_handle(raw: object) -> str:
    """Canonicalise a handle so @Name, name, and instagram.com/name all match.

    Lowercased, scheme/domain/@ stripped, trailing slash and query removed.
    Returns '' when there is nothing usable (A1, build-critical).
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return ""
    s = _HANDLE_STRIP.sub("", s)
    s = s.split("?")[0].split("/")[0]   # drop any path/query remnants
    s = s.strip().lstrip("@").rstrip("/").lower()
    return s


# --- date parsing (shared by engine/scoring) ---------------------------------
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y")
_MONTH_ONLY = ("%b %d", "%B %d")


def parse_date(raw: object) -> Optional[date]:
    """Parse the messy mixed date formats in the data.

    Handles ISO, dd/mm/yyyy, 'Dec 29' / 'Feb 1' (no year — inferred from the
    Dec-2025…Feb-2026 window the data lives in). Returns None when unparseable.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in _MONTH_ONLY:
        try:
            d = datetime.strptime(s, fmt)
            # No year in the string: months Sep–Dec are 2025, Jan–Aug are 2026.
            year = 2025 if d.month >= 9 else 2026
            return date(year, d.month, d.day)
        except ValueError:
            continue
    return None


# --- A6/A3: numeric coercion -------------------------------------------------
def to_number(raw: object) -> Optional[float]:
    """Parse a plain numeric cell; blank/garbage → None (scored as median later)."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_spend(raw: object) -> Tuple[Optional[float], bool]:
    """Parse '£5,170' / '9000' / '' → (value, is_cap).

    A3: £9,000 / 9000 is a data cap meaning 'unknown-high', flagged so ties break
    on velocity rather than letting a sentinel dominate.
    """
    if raw is None:
        return None, False
    s = str(raw).strip().replace("£", "").replace(",", "").strip()
    if not s or s.lower() == "nan":
        return None, False
    try:
        val = float(s)
    except ValueError:
        return None, False
    if val == 0:
        return None, False
    return val, val >= config.SPEND_CAP_SENTINEL


# --- A7/A10: email -----------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_JUNK_LOCAL = {"test", "asdf", "noemail", "none", "na", "n/a", "x"}
_JUNK_DOMAINS = {"test.com", "example.com", "test.test", "email.com", "domain.com"}


def clean_email(raw: object) -> Tuple[Optional[str], bool, Optional[str]]:
    """Repair obvious typos, validate, and flag junk.

    Returns (cleaned_or_None, is_valid, flag_or_None).
    - 'ines@@hotmail.com' → 'ines@hotmail.com' (A10, obvious double-@ repair).
    - 'info@shop.com' is fine (A7).
    - 'test@test.com' style → flagged junk.
    - Uncertain repairs are NOT guessed — flagged for manual review instead.
    """
    if raw is None:
        return None, False, None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None, False, None
    repaired = re.sub(r"@{2,}", "@", s)          # collapse '@@' → '@'
    repaired = repaired.replace(" ", "").lower()
    if not _EMAIL_RE.match(repaired):
        return None, False, "email_unparseable"
    local, domain = repaired.split("@", 1)
    if local in _JUNK_LOCAL or domain in _JUNK_DOMAINS or local == domain.split(".")[0] == "test":
        return repaired, False, "email_junk"
    return repaired, True, None


# --- A8/A9: phone ------------------------------------------------------------
def clean_phone(raw: object) -> Tuple[Optional[str], bool, Optional[str]]:
    """Normalise to a dialable string and decide if it is usable.

    A9: numeric/float cells are cast to string first. A8: anything with too few
    digits after cleaning is unusable → no call task + a flag.
    Returns (normalised_or_None, is_valid, flag_or_None).
    """
    if raw is None:
        return None, False, None
    s = str(raw).strip()
    if s.endswith(".0"):                  # pandas may read a numeric phone as float
        s = s[:-2]
    if not s or s.lower() == "nan":
        return None, False, None
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):                # 0044... international prefix → +
        digits_intl = digits.lstrip("0")
        normalised = "+" + digits_intl
    elif s.startswith("+"):
        normalised = "+" + digits
    else:
        normalised = s.strip()
    # A8: need a plausible number of digits to be dialable.
    if len(digits) < 9 or len(digits) > 15:
        return None, False, "phone_unusable"
    return normalised, True, None


# --- stage normalisation -----------------------------------------------------
def normalize_stage(raw: object) -> Tuple[str, Optional[str]]:
    """Map messy stage text to the canonical vocabulary.

    Returns (canonical_stage, flag_or_None). Unknown stage → 'new' + a flag so it
    surfaces for review rather than silently mis-routing.
    """
    if raw is None:
        return "new", "stage_missing"
    s = str(raw).strip().lower()
    if not s or s == "nan":
        return "new", "stage_missing"
    if s in config.STAGE_CANON:
        return config.STAGE_CANON[s], None
    return "new", f"stage_unrecognised:{s[:20]}"


# --- orchestration -----------------------------------------------------------
def clean_dataframe(df: pd.DataFrame, demo_today: date) -> pd.DataFrame:
    """Add cleaned columns + data_quality_flags to a raw pipeline frame."""
    df = df.copy()
    # Guarantee every expected source column exists (I5: missing optional cols).
    expected = [
        "lead_id", "source", "handle", "store_name", "contact_name", "email",
        "phone", "city", "country", "followers", "active_listings",
        "avg_listing_price_gbp", "sales_velocity_30d", "est_monthly_spend_gbp",
        "stage", "first_seen_date", "last_touch_date", "num_touches",
        "last_inbound_text", "assigned_bdr", "notes",
    ]
    for col in expected:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")

    records = []
    for _, row in df.iterrows():
        flags: list[str] = []

        handle_clean = canon_handle(row["handle"])
        store_clean = str(row["store_name"]).strip()
        city_clean = str(row["city"]).strip()
        country_clean = str(row["country"]).strip().upper()

        email_clean, email_valid, email_flag = clean_email(row["email"])
        if email_flag:
            flags.append(email_flag)
        phone_clean, phone_valid, phone_flag = clean_phone(row["phone"])
        if phone_flag:
            flags.append(phone_flag)

        spend_clean, spend_is_cap = clean_spend(row["est_monthly_spend_gbp"])
        followers_clean = to_number(row["followers"])
        velocity_clean = to_number(row["sales_velocity_30d"])
        listings_clean = to_number(row["active_listings"])
        avg_price_clean = to_number(row["avg_listing_price_gbp"])

        stage, stage_flag = normalize_stage(row["stage"])
        if stage_flag:
            flags.append(stage_flag)

        first_seen = parse_date(row["first_seen_date"])
        last_touch = parse_date(row["last_touch_date"])

        # A4: last_touch earlier than first_seen → unreliable; use first_seen.
        last_touch_reliable = True
        if first_seen and last_touch and last_touch < first_seen:
            last_touch_reliable = False
            flags.append("last_touch_before_first_seen")

        # A5: first_seen in the future → exclude until that date arrives.
        first_seen_future = bool(first_seen and first_seen > demo_today)
        if first_seen_future:
            flags.append("first_seen_future")

        num_touches = to_number(row["num_touches"]) or 0

        records.append({
            **{c: row[c] for c in expected},
            "handle_clean": handle_clean,
            "store_clean": store_clean,
            "city_clean": city_clean,
            "country_clean": country_clean,
            "email_clean": email_clean or "",
            "email_valid": email_valid,
            "phone_clean": phone_clean or "",
            "phone_valid": phone_valid,
            "spend_clean": spend_clean,
            "spend_is_cap": spend_is_cap,
            "followers_clean": followers_clean,
            "velocity_clean": velocity_clean,
            "listings_clean": listings_clean,
            "avg_price_clean": avg_price_clean,
            "stage": stage,
            "stage_raw": row["stage"],
            "first_seen": first_seen,
            "last_touch": last_touch,
            "last_touch_reliable": last_touch_reliable,
            "first_seen_future": first_seen_future,
            "num_touches": int(num_touches),
            "data_quality_flags": flags,
        })

    return pd.DataFrame.from_records(records)
