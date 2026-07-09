# -*- coding: utf-8 -*-
"""
utils.py — Shared cleaning primitives for the Logistics Control Tower.

Every cleaning function used anywhere in the project (ETL layer, business
logic layer) lives here ONCE. The previous codebase defined
clean_container_strict() four separate times inside logic_cloud.py alone
(identical body each time) and a fifth time in build_dashboard_data.py —
this file exists specifically to eliminate that duplication per the
project's own engineering rule: "Never duplicate business logic."
"""

import re
import numpy as np
import pandas as pd
from pathlib import Path
from lxml import etree

# =============================================================================
# ISO 6346 CONTAINER EXTRACTION
# =============================================================================
# Raw "Container #" fields across source files are NOT clean container IDs.
# They mix real ISO codes with courier tracking numbers (UPS/FedEx/DHL),
# multi-PO consolidation notes ("5 PO SEGU2963797"), and bare carrier labels
# ("TARGET EXPRESS", "CM TRUCK"). A regex search (not equality) for the ISO
# 6346 pattern — 4 letters + 7 digits — correctly extracts the real code even
# when it's embedded in noise, and naturally returns no match for carrier
# tracking numbers (which don't follow this shape). Confirmed against real
# data during discovery: this logic is sound and is kept as-is.

_ISO_PATTERN = re.compile(r"\b[A-Z]{4}[0-9]{7}\b")
_NON_CONTAINER_MARKERS = ["TRUCK", "L&S", "R&L", "UPS", "FEDEX", "FED EX",
                          "DHL", "TARGET", "XPO", "DAYLIGHT", "TFORCE",
                          "COOPER", "SEFL", "FLATBED"]

# Matches both "AIR FREIGHT" and "AIRFREIGHT" (no space) — real data uses
# both forms interchangeably (confirmed: 'AIRFREIGHT 057-53762310',
# 'AIR FREIGHT SHIPMENT', 'Freight-AIR FREIGHT'). The original pattern only
# matched the spaced form, silently dropping every no-space row as
# "no container" during cleaning — a real bug, not just a style choice.
_AIR_FREIGHT_MARKER = re.compile(r"AIR\s*FREIGHT", re.IGNORECASE)

# IATA Air Waybill number: 3-digit airline prefix + dash + 8-digit serial
# (e.g. "057-53762310", "001-80805841" from "AWB : 001-80805841"). Where
# present, this uniquely identifies one specific air shipment — exactly
# like an ISO 6346 code does for an ocean container — so it should be used
# as the shipment identifier instead of collapsing every air freight row
# into one generic, unidentifiable "AIR FREIGHT" bucket.
_AWB_PATTERN = re.compile(r"\b\d{3}-\d{8}\b")


def clean_container(val, keep_air_freight_marker=False):
    """
    Extract a clean shipment identifier from a noisy raw field.

    Returns, in priority order:
        - The extracted ISO 6346 ocean container code (e.g. 'MEDU2304983'), or
        - (if keep_air_freight_marker=True and the text indicates an air
          shipment) an identifiable Air Waybill reference like 'AWB
          057-53762310' when one is present in the text, or
        - 'AIR FREIGHT' as a fallback when the text indicates an air
          shipment but no AWB number can be extracted — a genuinely
          unidentifiable air shipment is still a valid state (no container
          ID exists for air freight by definition), not missing data, or
        - np.nan if no identifier of any kind can be determined.
    """
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    text = str(val).upper().strip()

    if keep_air_freight_marker and _AIR_FREIGHT_MARKER.search(text):
        awb_match = _AWB_PATTERN.search(text)
        if awb_match:
            return f"AWB {awb_match.group(0)}"
        return "AIR FREIGHT"

    match = _ISO_PATTERN.search(text)
    if match:
        return match.group(0)

    # No ISO pattern found — if it's an obvious non-container carrier label,
    # this is an expected null (parcel/LTL shipment), not a data error.
    return np.nan


# =============================================================================
# GENERIC FIELD CLEANERS
# =============================================================================

def standardize_columns(df):
    """
    snake_case every column name: 'Container #' -> 'container'.
    Also drops columns that end up with a blank name (NetSuite exports
    routinely have trailing 'Unnamed: N' columns that collapse to '' after
    stripping punctuation) — left in place, these create duplicate-label
    columns that break any later reindex/merge on the frame.
    """
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )
    df = df.loc[:, df.columns != ""]
    return df


def clean_currency(series):
    return (
        series.astype(str)
        .str.replace(r"[$,]", "", regex=True)
        .str.replace(r"\(([^)]+)\)", r"-\1", regex=True)
        .str.strip()
        .replace("", np.nan)
        .pipe(pd.to_numeric, errors="coerce")
    )


def clean_date(series):
    """
    CRITICAL FIX: pandas' default date parsing infers a single format from
    the first non-null value, then applies that fixed-width format to every
    other value. Because these NetSuite exports write day-of-month WITHOUT
    zero-padding ("Jun 5, 2026" vs "May 29, 2026"), any date whose day
    happens to be single-digit silently becomes NaT the moment a prior
    double-digit-day date has already fixed the inferred format. This is a
    real, high-impact bug: it was silently dropping the majority of values
    in every date column across the OLD pipeline (Port ETA, LFD, ETD, PO
    Date, Ship B/L Date, Invoice Date, etc. — every column that reused the
    same clean_date() pattern), not just the ship_b_l_date field the team
    had already flagged. format="mixed" parses each value independently
    instead of locking to one inferred pattern.
    """
    return pd.to_datetime(series, errors="coerce", format="mixed").dt.normalize()


def clean_numeric(series):
    return (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("\xa0", "", regex=False)
        .str.strip()
        .replace("", np.nan)
        .pipe(pd.to_numeric, errors="coerce")
    )


def clean_text(series):
    cleaned = series.astype(str).str.strip()
    return cleaned.replace({"": np.nan, "nan": np.nan, "None": np.nan, "NaN": np.nan})


def is_pending(series):
    """
    Business rule: any invoice/bill number containing 'PEND' in any form
    (PEND, PENDING, PEND123, ABC-PEND) is a placeholder invoice, not a
    missing one. Case-insensitive substring match per the domain skill.
    """
    return series.astype(str).str.contains("PEND", case=False, na=False)


# =============================================================================
# CONTAINER RESOLUTION WATERFALL
# =============================================================================
# A row is only valid if it has a resolvable container. Try these paths
# in order (stopping at first match):
#   1. Direct extraction from container field (ISO 6346 regex)
#   2. SIPL linkage (look up container from another row with same SIPL)
#   3. PO linkage (look up container from another row with same PO)
#   4. Notes/description extraction (regex search in freeform text)
# If none resolve, return None and log for audit.

def build_container_lookup(sources_dict):
    """
    Build a global lookup table from multiple sources to enable container
    resolution via SIPL/PO linkage.

    Args:
        sources_dict: dict mapping source name -> DataFrame
                      Expected keys: 'in_transit', 'inventory', 'bills'

    Returns:
        dict with keys:
          - 'sipl_to_container': {sipl -> set of containers}
          - 'po_to_container': {po -> set of containers}
          - 'all_rows': full DataFrame with resolved containers
    """
    lookup = {
        'sipl_to_container': {},
        'po_to_container': {},
        'all_rows': pd.DataFrame()
    }

    combined_rows = []

    for source_name, df in sources_dict.items():
        if df.empty:
            continue

        df = df.copy()

        # Get container column if it exists
        container_col = [c for c in df.columns if 'container' in c]
        if container_col:
            df['_container_clean'] = df[container_col[0]].apply(clean_container)
        else:
            df['_container_clean'] = np.nan

        # Get SIPL column if it exists
        sipl_col = [c for c in df.columns if 'sipl' in c]
        sipl_val = sipl_col[0] if sipl_col else None

        # Get PO column if it exists
        po_col = [c for c in df.columns if 'po' in c and c != 'po_date']
        po_val = po_col[0] if po_col else None

        # Build lookup mappings for rows with direct container match
        for idx, row in df.iterrows():
            container = row['_container_clean']
            if pd.notna(container):
                if sipl_val and pd.notna(row.get(sipl_val)):
                    sipl = str(row[sipl_val]).strip().upper()
                    if sipl not in lookup['sipl_to_container']:
                        lookup['sipl_to_container'][sipl] = set()
                    lookup['sipl_to_container'][sipl].add(container)

                if po_val and pd.notna(row.get(po_val)):
                    po = str(row[po_val]).strip().upper()
                    if po not in lookup['po_to_container']:
                        lookup['po_to_container'][po] = set()
                    lookup['po_to_container'][po].add(container)

        combined_rows.append(df)

    if combined_rows:
        lookup['all_rows'] = pd.concat(combined_rows, ignore_index=True)

    return lookup


def resolve_container(row, lookup, debug=False):
    """
    Resolve a container ID using the waterfall approach.

    Args:
        row: pandas Series with (at minimum) '_container_clean', 'sipl*', 'po' fields
        lookup: dict from build_container_lookup()
        debug: if True, return (container, resolution_path) tuple for diagnostics

    Returns:
        Resolved container string, or None if unresolvable
        If debug=True, returns (container, path) where path is 'direct'/'sipl'/'po'/None
    """

    # Step 1: Direct extraction
    if pd.notna(row.get('_container_clean')):
        container = row['_container_clean']
        if debug:
            return (container, 'direct')
        return container

    # Step 2: SIPL linkage (try all sipl* columns)
    sipl_val = None
    # Try direct 'sipl' first, then look for sipl* variants
    if pd.notna(row.get('sipl')):
        sipl_val = row.get('sipl')
    else:
        for col in row.index:
            if 'sipl' in str(col).lower() and pd.notna(row[col]):
                sipl_val = row[col]
                break

    if pd.notna(sipl_val):
        sipl = str(sipl_val).strip().upper()
        if sipl and sipl in lookup['sipl_to_container']:
            containers = lookup['sipl_to_container'][sipl]
            if containers:
                container = sorted(containers)[0]  # Pick first if multiple
                if debug:
                    return (container, 'sipl')
                return container

    # Step 3: PO linkage
    po_val = row.get('po')
    if pd.notna(po_val):
        po = str(po_val).strip().upper()
        if po and po in lookup['po_to_container']:
            containers = lookup['po_to_container'][po]
            if containers:
                container = sorted(containers)[0]
                if debug:
                    return (container, 'po')
                return container

    # Step 4: Notes/description extraction (already embedded in _container_clean)
    # This would be done in preprocessing, so if we get here it wasn't found

    if debug:
        return (None, None)
    return None


# =============================================================================
# NETSUITE HTML EXPORT LOADER
# =============================================================================
# NetSuite "export to Excel" saved searches are actually HTML tables with a
# .xls extension. This parses them directly and finds the real data table
# (skipping title/filter-summary tables NetSuite prepends).

def load_html_table(path: Path) -> pd.DataFrame:
    parser = etree.HTMLParser(recover=True, encoding="utf-8")
    with open(path, "rb") as f:
        tree = etree.parse(f, parser)
    tables = tree.xpath("//table")
    if not tables:
        raise ValueError(f"No <table> elements found in {path.name}")

    # The real data table is the largest one on the page.
    best_idx, best_rows = 0, -1
    parsed_tables = []
    for t in tables:
        rows = []
        for tr in t.xpath(".//tr"):
            cells = tr.xpath("./th|./td")
            row = ["".join(c.itertext()).strip() for c in cells]
            if any(row):
                rows.append(row)
        parsed_tables.append(rows)
        if len(rows) > best_rows:
            best_rows, best_idx = len(rows), len(parsed_tables) - 1

    rows = parsed_tables[best_idx]
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    header_idx = 0
    for i, row in enumerate(rows):
        non_empty = sum(1 for c in row if c)
        if len(row) > 1 and non_empty / len(row) >= 0.5:
            header_idx = i
            break

    df = pd.DataFrame(rows[header_idx + 1:], columns=rows[header_idx])
    # Drop NetSuite footer artifacts (BALANCE FORWARD header rows, PAGE
    # TOTALS / REPORT TOTALS footer rows) that would otherwise pollute
    # aggregations. Confirmed real risk, not theoretical: a "REPORT TOTALS"
    # row in Inventory In Transit - Detail.xls has its grand-total dollar
    # string sitting in the SIPL column (cells are right-shifted relative
    # to the header on that summary row) — if left in, it would corrupt any
    # groupby/join keyed on "sipl" with a literal "$5,440,568.03" value.
    first_col = df.columns[0]
    junk_mask = df[first_col].astype(str).str.upper().str.strip().isin(
        ["BALANCE FORWARD", "PAGE TOTALS", "REPORT TOTALS", ""]
    )
    df = df[~junk_mask].reset_index(drop=True)
    return df


# =============================================================================
# GL DESCRIPTION → CATEGORY CLASSIFICATION
# =============================================================================
# Normalize free-text GL description into one of the four required
# freight-cost categories (OF/CUSTOMS/DUTY/DRAYAGE) or flag as ACCESSORIAL
# (service charges not directly freight cost). Used by both build_dashboard_data.py
# (ETL classification) and gl_insights.py (exploratory dashboard).

REQUIRED_CATEGORIES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]
ACCESSORIAL_LABEL = "ACCESSORIAL"

def normalize_category(text):
    """
    Classify a GL description into one of: OF, CUSTOMS, DUTY, DRAYAGE,
    ACCESSORIAL, or None (if uncategorizable).

    Handles abbreviations, full text, fuzzy matching, and known junk patterns.
    Requires rapidfuzz to be installed for fuzzy matching fallback; if not
    available, fuzzy matching is skipped (category is None if no exact match).
    """
    if pd.isna(text):
        return None

    t = str(text).strip().upper()

    # Reject known junk patterns early
    if t == "" or t in {"MISC", "PO", "AMS", "ISF", "ISC"} or re.match(r"^PO\s*#", t):
        pass

    # Check for specific category keywords (abbreviations or full text)
    if t == "OF" or "OCEAN FREIGHT" in t or "AIR FREIGHT" in t or "AIRFREIGHT" in t:
        return "OF"
    if "CUSTOM" in t or "BROKERAGE" in t:
        return "CUSTOMS"
    if "DUTY" in t:
        return "DUTY"
    if "DRAY" in t:
        return "DRAYAGE"

    # Reject PO-number-only lines
    if re.match(r"^PO\s*#?\s*\d", t):
        return None

    # Check for known accessorial (service, not freight) terms
    accessorial_terms = ["DETENTION", "DEMURRAGE", "PER DIEM", "CHASSIS", "EXAM",
                          "TERMINAL", "ADMIN FEE", "MISC", "CREDIT MEMO", "AMS",
                          "ISF", "ISC", "FLATBED", "PER PULL", "DAMAGE"]
    if any(term in t for term in accessorial_terms):
        return ACCESSORIAL_LABEL

    # Fuzzy match as last resort (if rapidfuzz available)
    try:
        from rapidfuzz import process, fuzz
        match = process.extractOne(t, REQUIRED_CATEGORIES, scorer=fuzz.ratio)
        if match and match[1] >= 85:
            return match[0]
    except ImportError:
        pass

    return None


# =============================================================================
# VENDOR → CATEGORY INFERENCE MAPPING
# =============================================================================
# For pending bills without a real invoice number (category unknown), infer the
# category from historical GL data: which categories does this vendor supply?
# If a vendor's GL entries are dominated by one category (≥90%), use that;
# if a vendor supplies multiple categories equally, mark AMBIGUOUS_VENDOR.

def build_vendor_category_mapping(gl_freight, required_categories, threshold=0.90):
    """
    Build a vendor → category mapping from GL data.

    Args:
        gl_freight: DataFrame with columns 'party' (vendor) and 'category'
                   (already classified via normalize_category)
        required_categories: list of expected category values (e.g. ["OF", "CUSTOMS", "DUTY", "DRAYAGE"])
        threshold: confidence threshold (default 0.90 = ≥90% of vendor's GL lines for one category)

    Returns:
        dict mapping vendor name (string) → category (string) or 'AMBIGUOUS_VENDOR'
        Vendors with no classified GL entries are excluded.
    """
    # Filter to only classified GL entries (category is not None/NaN)
    gl_classified = gl_freight[gl_freight['category'].notna()].copy()
    # Exclude ACCESSORIAL/other non-required categories so that noise (MISC,
    # ISF, AMS, etc.) doesn't dilute the vendor's dominance calculation for
    # the 4 required categories.
    gl_classified = gl_classified[gl_classified['category'].isin(required_categories)]

    if gl_classified.empty:
        return {}

    # Group by vendor (party) and count category occurrences
    vendor_category_counts = (
        gl_classified.groupby(['party', 'category'])
        .size()
        .reset_index(name='count')
    )

    # Compute total per vendor
    vendor_totals = vendor_category_counts.groupby('party')['count'].sum().reset_index(name='total')

    # Join to compute percentage
    vendor_category_counts = vendor_category_counts.merge(vendor_totals, on='party')
    vendor_category_counts['pct'] = vendor_category_counts['count'] / vendor_category_counts['total']

    # For each vendor, find if one category dominates ≥threshold
    mapping = {}
    for vendor in vendor_category_counts['party'].unique():
        vendor_data = vendor_category_counts[vendor_category_counts['party'] == vendor]
        # Sort by percentage descending
        vendor_data = vendor_data.sort_values('pct', ascending=False)

        top_category = vendor_data.iloc[0]['category']
        top_pct = vendor_data.iloc[0]['pct']

        if top_pct >= threshold:
            # Vendor is dominated by one category
            mapping[vendor] = top_category
        else:
            # Vendor spans multiple categories → ambiguous
            mapping[vendor] = 'AMBIGUOUS_VENDOR'

    return mapping


# =============================================================================
# PLACEHOLDER / JUNK INVOICE NUMBER DETECTION
# =============================================================================
# Beyond literal "PEND*" markers (handled by is_pending()), Bills.xls and GL
# both contain clerical placeholder invoice numbers that are NOT real invoice
# IDs: "XENDING" (a corruption of PENDING), and bare sequence numbers used as
# lazy placeholders ("1", "2", "12", "Posted", "Duplicate"). Confirmed against
# real data: these values repeat across unrelated containers/vendors, so
# joining on them (e.g. Bills.bill_inv == GL.invoice) produces false-positive
# matches. Used by bills_insights.py and build_container_ledger() below.

JUNK_INVOICE_MARKERS = {"XENDING", "1", "2", "12", "POSTED", "DUPLICATE"}


def is_junk_invoice(series):
    """True where the invoice number is a known placeholder (not PEND*-based)."""
    return series.astype(str).str.strip().str.upper().isin(JUNK_INVOICE_MARKERS)


# =============================================================================
# BILLS.XLS CLEANING PIPELINE
# =============================================================================
# Single source of truth for cleaning Bills.xls, shared by bills_insights.py
# and container_insights.py. "No container = useless row" per project rule —
# any row that can't resolve to a container (or a legitimate Air Freight
# marker) is dropped; everything else is flagged, not dropped, so row counts
# stay reconcilable to the source export.

def clean_bills_dataframe(bills_raw):
    """
    Clean a raw Bills.xls DataFrame (already loaded via load_html_table).

    Returns:
        (bills_clean, original_count, final_count) — cleaned DataFrame plus
        row counts for the caller to report a cleaning waterfall.
    """
    original_count = len(bills_raw)
    bills = standardize_columns(bills_raw)

    # Step 1: Remove rows with no container
    bills = bills[bills["container"].notna() & (bills["container"].astype(str).str.strip() != "")]

    # Step 2: Remove rows with no invoice number
    bills = bills[bills["bill_inv"].notna() & (bills["bill_inv"].astype(str).str.strip() != "")]

    # Step 3: Remove exact duplicate bills
    bills = bills.drop_duplicates(subset=["container", "bill_inv"], keep="first")

    # Step 4: Clean container — extract ISO 6346, keep Air Freight as its own category
    bills["container_clean"] = bills["container"].apply(lambda x: clean_container(x, keep_air_freight_marker=True))
    bills = bills[bills["container_clean"].notna()]

    # Step 5: Clean currency fields
    for col in ["amount", "balance_due", "sipl_amount"]:
        if col in bills.columns:
            bills[col] = clean_currency(bills[col])

    # Step 6: Clean date fields
    for col in bills.columns:
        if ("date" in col.lower() or "dt" in col.lower()) and bills[col].dtype == "object":
            bills[col] = clean_date(bills[col])

    # Step 7: Flag anomalies (keep them, don't drop)
    bills["has_zero_amount"] = (bills["amount"] == 0) | bills["amount"].isna()
    bills["is_pending_literal"] = is_pending(bills["bill_inv"])
    bills["is_placeholder_junk"] = is_junk_invoice(bills["bill_inv"])
    # Broad flag: true for ANY air shipment (generic unidentified bucket OR
    # an individually-identified AWB reference) — used for reporting air vs.
    # ocean freight generally. Narrower than "is unidentifiable", which is
    # what callers doing container-level aggregation should check instead
    # (container == "AIR FREIGHT" specifically) so that AWB-identified air
    # shipments can still be tracked individually like any other container.
    bills["is_air_freight"] = (
        (bills["container_clean"] == "AIR FREIGHT")
        | bills["container_clean"].astype(str).str.startswith("AWB ")
    )

    # Drop the original noisy container column, promote the cleaned one
    bills = bills.drop(columns=["container"]).rename(columns={"container_clean": "container"})

    final_count = len(bills)
    return bills, original_count, final_count


# =============================================================================
# GL 1275 + 1313 CLEANING PIPELINE
# =============================================================================
# Single source of truth for cleaning/combining the two GL accounts, shared
# by gl_insights.py and container_insights.py.

def clean_gl_dataframe(gl_1275_raw, gl_1313_raw):
    """
    Clean and combine raw GL 1275 + 1313 DataFrames (already loaded via
    load_html_table). Tags each row with its source account.

    Returns:
        Combined, cleaned GL DataFrame with an 'account' column
        ("1275 - Capitalized" / "1313 - Prepaid").
    """
    gl_1275 = standardize_columns(gl_1275_raw)
    gl_1313 = standardize_columns(gl_1313_raw)

    gl_1275["account"] = "1275 - Capitalized"
    gl_1313["account"] = "1313 - Prepaid"

    gl = pd.concat([gl_1275, gl_1313], ignore_index=True)

    # Clean currency fields
    for col in ["debit", "credit", "balance"]:
        if col in gl.columns:
            gl[col] = clean_currency(gl[col])

    gl["net_amount"] = gl["debit"].fillna(0) - gl["credit"].fillna(0)

    if "date" in gl.columns:
        gl["date"] = clean_date(gl["date"])

    for col in ["party", "description"]:
        if col in gl.columns:
            gl[col] = clean_text(gl[col])

    # Fill missing category with an explicit label — pandas groupby() drops
    # NaN groups by default, which would silently exclude uncategorized rows
    # from every category breakdown/pie chart instead of showing them.
    gl["category"] = gl["description"].apply(normalize_category).fillna("Uncategorized")

    gl["is_adjustment"] = gl["description"].astype(str).str.contains(
        "ADJUSTMENT|CREDIT MEMO|REVERSAL|VOID", case=False, na=False
    ) | (gl["type"].isin(["Credit Memo", "Supplier Credit Memo"]))

    # Week column for time series (created before any account split so both
    # halves keep it)
    if "date" in gl.columns:
        gl["week"] = gl["date"].dt.to_period("W")

    return gl


# =============================================================================
# CONTAINER-LEVEL LEDGER (Bills ↔ GL merge)
# =============================================================================
# Join Bills to GL via invoice number, scoped to an operational date window,
# and aggregate to container level. GL has no container field of its own —
# this is the deliberate, deferred join that makes container-level GL
# insight possible at all.

def build_container_ledger(bills_clean, gl_clean, start, end):
    """
    Merge cleaned Bills and GL data into a container-level ledger.

    Args:
        bills_clean: output of clean_bills_dataframe() (first element of the tuple)
        gl_clean: output of clean_gl_dataframe()
        start, end: pd.Timestamp bounds (inclusive) for the operational window.
                    Bills filtered by invoice_dt, GL filtered by date.

    Returns:
        dict with keys:
          - 'container_ledger': one row per container active in the window,
            with billed/GL-confirmed totals, coverage %, category breakdown,
            vendor list, and (for 1275 matches) a 'notes' field.
          - 'matched_detail': row-level Bills↔GL matches (for drill-down /
            the 1275 traceability section).
          - 'unmatched_gl': GL rows in-window with a real invoice number
            that matched no Bill in-window — can't be tied to a container
            from this dataset, reported honestly rather than dropped.
    """
    # Only exclude the genuinely UNIDENTIFIED air freight bucket (literal
    # "AIR FREIGHT", no AWB number could be extracted) — grouping those
    # together would fabricate one bogus "container" out of many unrelated
    # shipments. An air shipment with a real AWB number (e.g. "AWB
    # 057-53762310") IS individually identifiable, exactly like an ISO
    # container, so it's kept and flows through the ledger like any other
    # container — this is what makes it searchable in get_container_detail().
    bills_window = bills_clean[
        (bills_clean["invoice_dt"] >= start) & (bills_clean["invoice_dt"] <= end)
        & (bills_clean["container"] != "AIR FREIGHT")
    ].copy()
    gl_window = gl_clean[
        (gl_clean["date"] >= start) & (gl_clean["date"] <= end)
    ].copy()

    # Matchable = has a real (non-placeholder, non-pending) invoice number.
    # Matching on placeholder values (e.g. "PENDING" == "PENDING") would be a
    # false-positive join, not a real transaction link — verified against
    # real data (naive join gave a suspicious 100% match rate driven by
    # placeholder collisions).
    bills_window["is_matchable"] = ~(bills_window["is_pending_literal"] | bills_window["is_placeholder_junk"])
    gl_window["is_matchable"] = ~(is_pending(gl_window["invoice"]) | is_junk_invoice(gl_window["invoice"]))

    matchable_bills = bills_window[bills_window["is_matchable"]]
    matchable_gl = gl_window[gl_window["is_matchable"]]

    matched_detail = matchable_bills.merge(
        matchable_gl,
        left_on="bill_inv",
        right_on="invoice",
        how="inner",
        suffixes=("_bill", "_gl"),
    )

    # notes field: only for matches sourced from the 1275 (Capitalized) account
    matched_detail["notes"] = ""
    is_1275 = matched_detail["account"] == "1275 - Capitalized"
    matched_detail.loc[is_1275, "notes"] = (
        "Container: " + matched_detail.loc[is_1275, "container"].astype(str)
        + " | SIPL: " + matched_detail.loc[is_1275, "sipl_inv"].astype(str)
    )

    # ---- Container-level aggregation ----
    total_billed = bills_window.groupby("container")["amount"].sum().rename("total_billed")
    total_gl_confirmed = matched_detail.groupby("container")["net_amount"].sum().rename("total_gl_confirmed")
    bill_count = bills_window.groupby("container").size().rename("bill_count")
    gl_matched_count = matched_detail.groupby("container").size().rename("gl_matched_count")
    awaiting_gl_count = (
        bills_window[~bills_window["is_matchable"]].groupby("container").size().rename("awaiting_gl_count")
    )
    pending_amount = (
        bills_window[~bills_window["is_matchable"]].groupby("container")["amount"].sum().rename("pending_amount")
    )
    # Delimiter must NOT be a comma — 24% of real vendor names contain a
    # comma as part of their own legal name (e.g. "Department of HomeLand
    # Security, Bureau of Customs and Border Protection", "EWI, Inc."), so
    # joining/splitting on ", " would fabricate fake duplicate vendor
    # fragments. " | " does not collide with any vendor name in this data.
    vendor_list = (
        bills_window.groupby("container")["non_inventory_vendor"]
        .apply(lambda s: " | ".join(sorted(set(s.dropna().astype(str)))))
        .rename("vendors")
    )
    notes_by_container = (
        matched_detail[matched_detail["notes"] != ""]
        .groupby("container")["notes"]
        .apply(lambda s: " ; ".join(sorted(set(s))))
        .rename("notes")
    )

    # Category breakdown pivoted to one column per category
    category_breakdown = (
        matched_detail.pivot_table(
            index="container", columns="category", values="net_amount", aggfunc="sum", fill_value=0
        )
    )

    container_ledger = pd.concat(
        [total_billed, total_gl_confirmed, bill_count, gl_matched_count, awaiting_gl_count, pending_amount, vendor_list, notes_by_container],
        axis=1,
    ).reset_index()

    container_ledger["total_gl_confirmed"] = container_ledger["total_gl_confirmed"].fillna(0)
    container_ledger["gl_matched_count"] = container_ledger["gl_matched_count"].fillna(0).astype(int)
    container_ledger["awaiting_gl_count"] = container_ledger["awaiting_gl_count"].fillna(0).astype(int)
    container_ledger["pending_amount"] = container_ledger["pending_amount"].fillna(0)
    container_ledger["notes"] = container_ledger["notes"].fillna("")
    container_ledger["gl_coverage_pct"] = np.where(
        container_ledger["total_billed"] > 0,
        100 * container_ledger["total_gl_confirmed"] / container_ledger["total_billed"],
        0,
    )

    container_ledger = container_ledger.merge(
        category_breakdown, on="container", how="left"
    )

    # ---- Unmatched GL: real invoice numbers with no Bills match in-window ----
    matched_gl_invoices = set(matched_detail["invoice"].dropna().unique())
    unmatched_gl = matchable_gl[~matchable_gl["invoice"].isin(matched_gl_invoices)]

    # ---- Pending/placeholder bills, at row level ----
    # A pending bill still has a known container (Bills always carries one)
    # even though it can't be matched to GL by invoice number. These must
    # stay visible — "doesn't match GL" is not the same as "unknown" or
    # "excluded". Surfaced separately so the dashboard can show exactly
    # which bill is pending for which container, not just a count.
    pending_bill_detail = bills_window[~bills_window["is_matchable"]][
        ["container", "sipl_inv", "bill_inv", "non_inventory_vendor", "amount", "invoice_dt"]
    ].copy()

    return {
        "container_ledger": container_ledger,
        "matched_detail": matched_detail,
        "unmatched_gl": unmatched_gl,
        "pending_bill_detail": pending_bill_detail,
    }


# =============================================================================
# SINGLE-CONTAINER LOOKUP (search feature)
# =============================================================================
# Full-history detail for one container — deliberately NOT limited to the
# operational window used by build_container_ledger(), since a manager
# searching a specific container wants the complete picture (every SIPL,
# every bill, ever), not just recent activity.

def get_container_detail(container_id, bills_clean, gl_clean, year=None):
    """
    Look up detail for a single container. A container ID is reused across
    many separate shipments ("runs") over its physical lifetime — a
    container number does not change year to year, so full, ungrouped
    history for a container mixes unrelated runs/vendors together and reads
    as if there are duplicate bills when there aren't. Two things address
    this: an optional year filter, and grouping the per-bill detail into
    per-run ("runs" = distinct SIPL) totals.

    Args:
        container_id: exact container ID to look up (case-sensitive, as
                       stored — caller should uppercase/strip user input first)
        bills_clean: output of clean_bills_dataframe() (first tuple element)
        gl_clean: output of clean_gl_dataframe()
        year: optional int (e.g. 2026). If given, restricts to bills whose
              invoice_dt falls in that year before computing anything else.
              If None, returns full history across all years.

    Returns:
        dict with keys:
          - 'found': bool — whether any bills exist for this container (in
            the requested year, if one was given)
          - 'available_years': sorted list (desc) of years with bills for
            this container, computed BEFORE the year filter is applied —
            always the full list, so the caller can populate a year picker
            regardless of which year is currently selected
          - 'sipl_list': sorted list of unique SIPL numbers tied to this container
          - 'bill_count', 'pending_count', 'matched_count': int summary counts
          - 'total_billed', 'total_gl_confirmed', 'gl_coverage_pct': float
          - 'bills_detail': DataFrame, one row per bill, with a 'status'
            column ('Confirmed' / 'Pending' / 'Awaiting GL Match')
          - 'category_breakdown': dict of category -> $ (from matched GL only)
          - 'vendors': sorted list of unique vendor names
          - 'runs': DataFrame, one row per SIPL ("run") — total cost, bill
            count, pending count, and date span for that specific run,
            answering "how much did this container cost per trip"
    """
    container_bills = bills_clean[bills_clean["container"] == container_id].copy()

    if container_bills.empty:
        return {"found": False, "container": container_id, "available_years": []}

    available_years = sorted(
        container_bills["invoice_dt"].dt.year.dropna().astype(int).unique().tolist(), reverse=True
    )

    if year is not None:
        container_bills = container_bills[container_bills["invoice_dt"].dt.year == year]
        if container_bills.empty:
            return {"found": False, "container": container_id, "available_years": available_years}

    # Defensive: group key must never silently drop rows with a missing SIPL
    container_bills["sipl_inv"] = container_bills["sipl_inv"].fillna("Unknown SIPL")

    container_bills["is_matchable"] = ~(container_bills["is_pending_literal"] | container_bills["is_placeholder_junk"])

    gl_matchable = gl_clean[~(is_pending(gl_clean["invoice"]) | is_junk_invoice(gl_clean["invoice"]))]
    matched = container_bills[container_bills["is_matchable"]].merge(
        gl_matchable, left_on="bill_inv", right_on="invoice", how="inner", suffixes=("_bill", "_gl")
    )

    # Per-bill status for the detail table
    matched_invoices = set(matched["bill_inv"].unique())

    def _status(row):
        if row["is_pending_literal"] or row["is_placeholder_junk"]:
            return "Pending"
        if row["bill_inv"] in matched_invoices:
            return "Confirmed"
        return "Awaiting GL Match"

    container_bills["status"] = container_bills.apply(_status, axis=1)

    total_billed = container_bills["amount"].sum()
    total_gl_confirmed = matched["net_amount"].sum()
    gl_coverage_pct = 100 * total_gl_confirmed / total_billed if total_billed > 0 else 0

    category_breakdown = matched.groupby("category")["net_amount"].sum().to_dict() if len(matched) > 0 else {}

    vendors = sorted(set(container_bills["non_inventory_vendor"].dropna().astype(str)))
    sipl_list = sorted(set(container_bills["sipl_inv"].dropna().astype(str)))

    # ---- Per-run (per-SIPL) breakdown: "how much did this container cost per trip" ----
    runs = container_bills.groupby("sipl_inv").agg(
        total_billed=("amount", "sum"),
        bill_count=("bill_inv", "count"),
        first_bill_date=("invoice_dt", "min"),
        last_bill_date=("invoice_dt", "max"),
    ).reset_index()
    pending_per_run = container_bills[container_bills["status"] == "Pending"].groupby("sipl_inv").size()
    runs["pending_count"] = runs["sipl_inv"].map(pending_per_run).fillna(0).astype(int)
    runs = runs.sort_values("first_bill_date", ascending=False).reset_index(drop=True)

    return {
        "found": True,
        "container": container_id,
        "available_years": available_years,
        "sipl_list": sipl_list,
        "sipl_count": len(sipl_list),
        "bill_count": len(container_bills),
        "pending_count": int((container_bills["status"] == "Pending").sum()),
        "matched_count": int((container_bills["status"] == "Confirmed").sum()),
        "awaiting_count": int((container_bills["status"] == "Awaiting GL Match").sum()),
        "total_billed": total_billed,
        "total_gl_confirmed": total_gl_confirmed,
        "gl_coverage_pct": gl_coverage_pct,
        "bills_detail": container_bills[
            ["bill_inv", "sipl_inv", "non_inventory_vendor", "amount", "invoice_dt", "status"]
        ].sort_values("invoice_dt", ascending=False),
        "category_breakdown": category_breakdown,
        "vendors": vendors,
        "runs": runs,
    }


# =============================================================================
# IN-TRANSIT LIST BY SIPL — CLEANING PIPELINE
# =============================================================================
# Single source of truth for cleaning "In-Transit List by SIPL.xls", shared
# by in_transit_insights.py. Scope is deliberately restricted to genuine
# ocean-container freight: confirmed against real data that the ~73% of
# rows with no resolvable ISO container are overwhelmingly domestic
# trucking/parcel tracking numbers (TRUCK FEDEXGRND, TRUCK UPS, TRUCK XPO,
# TRUCK DAYLIGHT, TRUCK R&L, TRUCK TFORCE, etc. — zero AWB/air-freight
# numbers found in this file), not international freight worth tracking
# for this purpose. Per explicit direction: rows without a valid container
# number are dropped, not merely flagged — domestic data is out of scope.

_INITIATED_ON_DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")


def clean_in_transit_dataframe(raw):
    """
    Clean a raw "In-Transit List by SIPL.xls" DataFrame (already loaded via
    load_html_table), restricted to rows with a valid ISO 6346 ocean
    container number. Domestic/parcel-tracked rows (no container) are
    dropped — confirmed this is ~73% of the file and is not the container
    freight this dashboard is scoped to track.

    Returns:
        (df_clean, original_count, final_count)
    """
    original_count = len(raw)
    df = standardize_columns(raw)

    # Rows with no SIPL are not real shipment records (mirrors the
    # load_html_table() footer-artifact guard, defensive in case a future
    # export has a footer variant that guard doesn't yet know about).
    df = df[df["sipl"].notna() & (df["sipl"].astype(str).str.strip() != "")]

    # "Initiated On 7/6/2026" or bare "7/6/2026" -> a real date. Regex
    # extraction handles both forms uniformly.
    extracted = df["initiated_on"].astype(str).str.extract(_INITIATED_ON_DATE)[0]
    df["initiated_on"] = pd.to_datetime(extracted, errors="coerce", format="mixed")

    # Container field mixes real ISO codes with truck/parcel tracking
    # numbers, same noisy shape as Bills.xls — reuse clean_container() as-is.
    df["container"] = df["container"].apply(lambda x: clean_container(x, keep_air_freight_marker=True))

    # Drop domestic/unresolvable rows. Generic "AIR FREIGHT" and AWB-style
    # entries are dropped too — confirmed zero AWB matches in this file, so
    # this is purely a defensive/consistency measure with Bills.xls, not
    # something expected to remove real international-air rows here.
    df = df[
        df["container"].notna()
        & (df["container"] != "AIR FREIGHT")
        & (~df["container"].astype(str).str.startswith("AWB "))
    ]

    for col in ["port_eta", "rail_eta", "location_eta", "eta_date", "lfd"]:
        if col in df.columns:
            df[col] = clean_date(df[col])

    for col in ["supplier", "ship_to_location", "purchase_location", "vessel",
                "sipl_status", "status", "fr_forwarder", "departure_port"]:
        if col in df.columns:
            df[col] = clean_text(df[col])

    # clean_text() turns blank cells into NaN, and value_counts() drops NaN
    # by default — without this, the ~10 shipments with no sipl_status on
    # file would silently vanish from every funnel/breakdown instead of
    # showing up as their own explicit "Unknown" slice (same fix applied to
    # GL's category field earlier in this project).
    df["sipl_status"] = df["sipl_status"].fillna("Unknown")

    # SIPL age: days since initiated. Computed relative to "today" at call
    # time (not cached), since this is a live operational metric, not a
    # static historical fact like Bills/GL cleaning.
    today = pd.Timestamp.today().normalize()
    df["sipl_age_days"] = (today - df["initiated_on"]).dt.days

    df["has_lfd"] = df["lfd"].notna()

    final_count = len(df)
    return df, original_count, final_count


# =============================================================================
# SIPL -> CONTAINER ROLLUP (standalone, no Inventory Detail merge needed)
# =============================================================================
# A container routinely carries multiple SIPLs (avg 1.81, confirmed against
# real data), so any COUNT-based view built directly on sipl_clean (pipeline
# funnel, LFD risk, freight forwarder concentration, sourcing/destination
# breakdowns) inflates relative to physical container counts. Verified: the
# "Scheduled for Delivery" stage shows 12 SIPLs but only 2 containers — a 6x
# inflation. This rollup is the container-level source of truth for
# in_transit_insights.py, mirroring container_consolidation in
# merge_sipl_inventory() but usable without the Inventory Detail file.

def build_sipl_container_rollup(sipl_clean):
    """
    One row per container, rolled up from the SIPL tracking list alone.

    Fields that are physical facts about the container (sipl_status, lfd,
    fr_forwarder, departure_port, ship_to_location) are deduplicated across
    sibling SIPLs sharing that container, with a `has_mixed_*`/
    `is_routing_consistent` flag raised whenever siblings genuinely
    disagree (verified real data: 0 mixed status, 1 mixed forwarder, 1
    routing-inconsistent container out of 124 — so this is rare, not the
    common case, but must be surfaced rather than silently picked).
    `sipl_age_days` uses the oldest (max) sibling, since that's the
    actionable "how long has any part of this container's process been
    open" signal.
    """
    g = sipl_clean.groupby("container")
    rollup = g.agg(
        sipl_count=("sipl", "nunique"),
        sipls=("sipl", lambda s: sorted(s.astype(str).unique())),
        status_nunique=("sipl_status", lambda s: s.dropna().nunique()),
        sipl_status=("sipl_status", lambda s: s.dropna().iloc[0] if s.dropna().size else "Unknown"),
        has_lfd=("has_lfd", "any"),
        lfd=("lfd", "min"),
        ff_nunique=("fr_forwarder", lambda s: s.dropna().nunique()),
        fr_forwarder=("fr_forwarder", lambda s: s.dropna().iloc[0] if s.dropna().size else None),
        dp_nunique=("departure_port", lambda s: s.dropna().nunique()),
        departure_port=("departure_port", lambda s: s.dropna().iloc[0] if s.dropna().size else None),
        st_nunique=("ship_to_location", lambda s: s.dropna().nunique()),
        ship_to_location=("ship_to_location", lambda s: s.dropna().iloc[0] if s.dropna().size else None),
        sipl_age_days=("sipl_age_days", "max"),
    ).reset_index()

    rollup["has_mixed_status"] = rollup["status_nunique"] > 1
    rollup["has_mixed_forwarder"] = rollup["ff_nunique"] > 1
    rollup["is_routing_consistent"] = (rollup["dp_nunique"] <= 1) & (rollup["st_nunique"] <= 1)
    rollup = rollup.drop(columns=["status_nunique", "ff_nunique", "dp_nunique", "st_nunique"])
    return rollup


# =============================================================================
# INVENTORY IN TRANSIT - DETAIL — CLEANING PIPELINE
# =============================================================================
# Single source of truth for cleaning "Inventory In Transit - Detail.xls",
# shared by inventory_detail_insights.py. Same container-required scope as
# clean_in_transit_dataframe(): confirmed against real data that the ~55%
# of rows with no resolvable ISO container are overwhelmingly domestic
# parcel tracking (UPS#..., FedEX#..., TARGET EXPRESS — zero AWB/air-freight
# numbers found in this file either), and that restricting to real-container
# rows keeps 95.5% of total dollar value ($5.19M of $5.44M) while dropping
# 55% of row count — the domestic/parcel rows are overwhelmingly low-value
# "Sample" line items, not meaningful freight being lost. Per explicit
# direction: rows without a valid container number are dropped, not merely
# flagged.

def clean_inventory_detail_dataframe(raw):
    """
    Clean a raw "Inventory In Transit - Detail.xls" DataFrame (already
    loaded via load_html_table — which itself now strips the "REPORT
    TOTALS" footer row this export includes), restricted to rows with a
    valid ISO 6346 ocean container number. Domestic/parcel-tracked rows
    (no container) are dropped — confirmed this is ~55% of rows but only
    ~4.5% of total dollar value.

    Returns:
        (df_clean, original_count, final_count)
    """
    original_count = len(raw)
    df = standardize_columns(raw)

    # Defensive, mirrors clean_in_transit_dataframe(): a row with no SIPL
    # cannot be a real product line (every genuine line item belongs to a
    # shipment) — catches any future footer/summary-row variant that
    # load_html_table()'s own guard doesn't yet recognize by name.
    df = df[df["sipl"].notna() & (df["sipl"].astype(str).str.strip() != "")]

    df["container"] = df["container"].apply(lambda x: clean_container(x, keep_air_freight_marker=True))

    # Drop domestic/unresolvable rows (see module note above for the
    # verified 95.5%-of-value-retained rationale).
    df = df[
        df["container"].notna()
        & (df["container"] != "AIR FREIGHT")
        & (~df["container"].astype(str).str.startswith("AWB "))
    ]

    for col in ["unit_cost", "total_cost"]:
        if col in df.columns:
            df[col] = clean_currency(df[col])

    for col in ["sipl_date", "req_ship_date", "ship_b_l_date", "eta_date"]:
        if col in df.columns:
            df[col] = clean_date(df[col])

    for col in ["name", "type", "category", "subcategory", "group", "supplier",
                "freight_forwarder", "departure_port", "arrival_port", "bill_to", "ship_to"]:
        if col in df.columns:
            df[col] = clean_text(df[col])

    df["quantity"] = clean_numeric(df["quantity"])

    # Defensive check for malformed category values (e.g. a stray numeric
    # value that leaked in from a misaligned report row) — flag rather than
    # silently including it in category breakdowns.
    df["category_is_malformed"] = df["category"].astype(str).str.match(r"^[\d,\.]+$", na=False)

    # clean_text() turns blank cells into NaN, and value_counts()/groupby()
    # drop NaN by default. `category` is used as a primary breakdown
    # dimension (unlike subcategory/group, which are intentionally shown as
    # partial views), so it must not silently lose rows.
    df["category"] = df["category"].fillna("Unknown")

    final_count = len(df)
    return df, original_count, final_count


# =============================================================================
# SIPL TRACKING ↔ INVENTORY DETAIL MERGE
# =============================================================================
# Both files share 'sipl' as a key. Verified against real data before
# building this: for the 208 SIPLs present in both (container-tracked)
# files, there are ZERO container-ID mismatches and ZERO departure-port
# mismatches — the two files agree perfectly wherever they overlap. Also
# verified container-level routing consistency (ship_to/arrival_port/
# departure_port shouldn't vary for one container's rows): 123 of 124
# container-tracked containers (99.2%) are fully self-consistent; the one
# exception is legitimate container reuse across two different SIPLs, not
# a data error.

_COUNTRY_FROM_PORT = re.compile(r",\s*([A-Z]{2})\s")


def extract_country_from_port(port_series):
    """Extract the 2-letter country code from a 'City, XX NNNNN' port string."""
    return port_series.astype(str).str.extract(_COUNTRY_FROM_PORT)[0]


def filter_inventory_to_tracked_sipls(inventory_clean, sipl_clean):
    """
    Drop Inventory Detail rows whose SIPL doesn't appear in the SIPL
    tracking list. The tracking list is the authoritative "currently
    active" shipment universe — an Inventory Detail row for a SIPL that has
    fallen off that list (e.g. already completed/delivered and dropped from
    tracking, or from a stale export) isn't part of the current operational
    picture and shouldn't count in Inventory Detail's own totals either.

    Verified against real data (2026-07-09): currently 0 of 415
    container-tracked Inventory Detail rows are affected — every SIPL in
    Inventory Detail already has a match in the tracking list today. This
    filter exists for correctness going forward (a future data pull could
    easily have stale rows), not because today's data currently needs it.

    Args:
        inventory_clean: output of clean_inventory_detail_dataframe() (first tuple element)
        sipl_clean: output of clean_in_transit_dataframe() (first tuple element)

    Returns:
        (df_filtered, original_count, final_count)
    """
    original_count = len(inventory_clean)
    tracked_sipls = set(sipl_clean["sipl"].astype(str).str.strip())
    df = inventory_clean[inventory_clean["sipl"].astype(str).str.strip().isin(tracked_sipls)].copy()
    final_count = len(df)
    return df, original_count, final_count


def merge_sipl_inventory(sipl_clean, inventory_clean):
    """
    Merge cleaned SIPL tracking data with cleaned Inventory Detail data via
    SIPL. Both inputs must already be container-scoped (outputs of
    clean_in_transit_dataframe() / clean_inventory_detail_dataframe()).

    Args:
        sipl_clean: output of clean_in_transit_dataframe() (first tuple element)
        inventory_clean: output of clean_inventory_detail_dataframe()

    Returns:
        dict with keys:
          - 'merged_detail': inner join, one row per inventory line item
            enriched with tracking status/dates (sipl_status, has_lfd, lfd,
            fr_forwarder, sipl_age_days). Only SIPLs present in both files.
            Includes a 'transit_days' column (ship_b_l_date -> eta_date),
            'has_transit_anomaly' flag (negative transit_days — ETA before
            the ship date, which is impossible and always a data error,
            not a real value), and 'is_on_water' (True when ship_b_l_date
            is populated and in the past — the Bill of Lading is issued
            once cargo is actually loaded, so this is a real "has sailed"
            signal, not a guess).
          - 'sipl_summary': one row per container-tracked SIPL (ALL of
            them, including the ones with no inventory detail — those get
            value=0/item_count=0 rather than being dropped), for value-at-
            risk analysis that shouldn't undercount by silently excluding
            undetailed SIPLs. Includes 'sailing_status' (has it sailed at
            all — "On the Water", "Not Yet Sailed", or "Cannot Determine
            (No Detail)") and the more precise 'physical_status', a
            six-way model of where the SIPL physically is RIGHT NOW,
            derived from dates, not from sipl_status alone (a manually-
            updated paperwork field that can lag physical reality by days).
            port_eta and location_eta measure two DIFFERENT sequential
            milestones (confirmed: location_eta is always >= port_eta —
            branch arrival happens after port arrival, ~7 days later on
            average) and are checked separately, not coalesced: port_eta
            determines "On the Water" vs. "Arrived, Processing"; location_eta
            (or sipl_status == "At branch") determines "Delivered (At
            Branch)". Values: "Not Yet Sailed", "On the Water" (departed,
            port_eta still ahead or exactly today), "Arrived, Processing"
            (past port_eta but not yet at branch — physically at port/in
            customs/awaiting drayage), "Delivered (At Branch)", "Cannot
            Determine (No Detail)" (no inventory match, no B/L date to
            check), or "Cannot Determine (No ETA Data)" (sailed, but no
            port_eta exists — genuinely unknown whether it's arrived, NOT assumed
            still on water just because status isn't "At branch" either —
            that would repeat the same status-can't-be-trusted mistake this
            whole model exists to avoid). 'is_currently_on_water' is True
            only for the "On the Water" physical_status bucket.
          - 'container_consolidation': one row per container (the grain to
            report "on the water" style counts at — a container carrying
            multiple SIPLs is one physical box, not N of them). Includes
            sipl_count, total_value, is_routing_consistent (ship_to/
            departure_port nunique <= 1 across the container's SIPLs — False
            means genuine reuse across unrelated shipments at different
            times), and container-level physical_status/ship_b_l_date/
            port_eta/location_eta derived from sipl_summary AFTER date
            propagation across routing-consistent containers' sibling SIPLs
            (see the propagation step above sipl_summary's status
            computation). has_mixed_status is True whenever a container's
            sibling SIPLs still disagree on physical_status after
            propagation — checked directly by comparing agreement, NOT
            inferred from is_routing_consistent, because that flag only
            checks ship_to/departure_port and does not guarantee sibling
            SIPLs share the same ship_b_l_date/port_eta (confirmed: 2
            containers are routing-consistent yet have genuinely conflicting
            non-null dates a week+ apart, which propagation correctly leaves
            unresolved rather than picking one). Whenever mixed,
            physical_status reports the most-recently-active SIPL's status
            as primary, flagged rather than silently picked. Also includes
            has_lfd/lfd (propagated the same way as the other dates — LFD is
            a port-side deadline that applies to the whole physical
            container, not per-SIPL), ship_to_location/departure_port (first
            non-null value — exact for routing-consistent containers), and
            suppliers (" | "-joined list — multiple suppliers' goods can
            legitimately share one consolidated container, not assumed to
            be a single value), and transit_days/has_transit_anomaly
            (ship_b_l_date -> port_eta, computed once per container from
            the already-container-level fields above, not re-derived from
            merged_detail's line-item eta_date which would count a
            container once per product line it carries).
    """
    overlap_cols = ["container", "departure_port", "eta_date", "supplier"]
    merged_detail = inventory_clean.merge(
        sipl_clean.drop(columns=[c for c in overlap_cols if c in sipl_clean.columns]),
        on="sipl", how="inner", suffixes=("", "_tracking")
    )

    merged_detail["transit_days"] = (merged_detail["eta_date"] - merged_detail["ship_b_l_date"]).dt.days
    merged_detail["has_transit_anomaly"] = merged_detail["transit_days"] < 0
    merged_detail["departure_country"] = extract_country_from_port(merged_detail["departure_port"])

    # "On the water" determination: a Bill of Lading is issued once cargo is
    # actually loaded onto the vessel, so a ship_b_l_date in the past is a
    # real signal the shipment has sailed — this is what actually matters
    # operationally (vs. still sitting at origin awaiting booking/loading).
    today = pd.Timestamp.today().normalize()
    merged_detail["is_on_water"] = merged_detail["ship_b_l_date"].notna() & (merged_detail["ship_b_l_date"] <= today)

    # sipl_summary: LEFT join so every container-tracked SIPL is visible,
    # even the ones with no inventory detail yet (value/item_count = 0,
    # not silently dropped from a value-at-risk view).
    value_by_sipl = inventory_clean.groupby("sipl").agg(
        total_value=("total_cost", "sum"), item_count=("total_cost", "count"),
        ship_b_l_date=("ship_b_l_date", "min"),
    )
    sipl_summary = sipl_clean.merge(value_by_sipl, on="sipl", how="left")
    sipl_summary["total_value"] = sipl_summary["total_value"].fillna(0)
    sipl_summary["item_count"] = sipl_summary["item_count"].fillna(0).astype(int)
    sipl_summary["has_detail"] = sipl_summary["item_count"] > 0

    # Routing consistency per container (same ship_to/departure_port across
    # every SIPL sharing it) — computed early because it gates whether dates
    # are safe to propagate across sibling SIPLs below. A routing-consistent
    # container is genuinely one voyage; a routing-inconsistent one is reuse
    # across unrelated shipments at different times, and must NOT have dates
    # shared across that boundary.
    routing_check = sipl_clean.groupby("container").agg(
        ship_to_nunique=("ship_to_location", lambda s: s.dropna().nunique()),
        departure_port_nunique=("departure_port", lambda s: s.dropna().nunique()),
    )
    routing_check["is_routing_consistent"] = (
        (routing_check["ship_to_nunique"] <= 1) & (routing_check["departure_port_nunique"] <= 1)
    )
    sipl_summary["is_routing_consistent"] = sipl_summary["container"].map(routing_check["is_routing_consistent"])

    # Propagate known dates across sibling SIPLs sharing a routing-consistent
    # container. Verified against real data: within a genuinely single-voyage
    # container, sibling SIPLs share the same ship_b_l_date/port_eta/
    # location_eta — but only SOME of a consolidated container's SIPL rows
    # may have a matching Inventory Detail record to confirm those dates
    # directly (ship_b_l_date only exists via that match). Confirmed example:
    # a 30-SIPL Genoa-origin container where 17 SIPLs individually confirm
    # ship_b_l_date=2026-07-01 / port_eta=2026-07-17 via their own Inventory
    # Detail match, and the other 13 have no match at all — they're the same
    # physical container on the same voyage, so the known dates apply to them
    # too. Routing-inconsistent containers (reuse across unrelated shipments)
    # are explicitly excluded from this fill.
    consistent_mask = sipl_summary["is_routing_consistent"]
    for col in ["ship_b_l_date", "port_eta", "location_eta", "lfd"]:
        propagated = sipl_summary.loc[consistent_mask].groupby("container")[col].transform("min")
        sipl_summary.loc[consistent_mask, col] = sipl_summary.loc[consistent_mask, col].fillna(propagated)
    sipl_summary["has_lfd"] = sipl_summary["lfd"].notna()

    # Has this SIPL sailed at all? "Cannot Determine" is an honest third
    # state for SIPLs with no inventory detail — and, after the propagation
    # above, no sibling on the same routing-consistent container either —
    # not folded into either "sailed" or "not sailed".
    def _sailing_status(row):
        if pd.isna(row["ship_b_l_date"]):
            return "Cannot Determine (No Detail)"
        return "On the Water" if row["ship_b_l_date"] <= today else "Not Yet Sailed"

    sipl_summary["sailing_status"] = sipl_summary.apply(_sailing_status, axis=1)

    # port_eta and location_eta are NOT interchangeable — they measure two
    # different, sequential milestones. Confirmed against real data: for
    # every SIPL where both are populated (205 of 225), location_eta is
    # ALWAYS >= port_eta (average gap ~7 days) — port_eta is arrival at the
    # US port; location_eta is arrival at the final branch, which happens
    # later, after customs clearance and drayage. Also confirmed the two
    # fields are blank/populated in perfect lockstep (0 rows have one
    # without the other), so there is no case where one usefully substitutes
    # for the other — each is checked for its own specific milestone below,
    # not coalesced together.
    #
    # "Has arrived at PORT" uses port_eta specifically, NOT sipl_status —
    # status is a manually-updated workflow field that can lag behind
    # physical reality by days. Verified: 36 SIPLs had already passed their
    # own port_eta (some by over a week) while still showing "D.O.s
    # Received" / "Scheduled for Delivery" / "On Hold" — none "At branch" —
    # so checking status alone would have called all 36 of these "still on
    # the water" when they'd physically already reached port.
    #
    # "Has reached the BRANCH" uses EITHER sipl_status == "At branch" OR a
    # passed location_eta — same not-trusting-status-alone principle applied
    # to the delivery milestone. Verified: 3 SIPLs have already passed both
    # port_eta and location_eta while still showing "SIPL Ready" (the
    # earliest status) — these turned out to be the same 3 SIPLs already
    # flagged as date anomalies (ETA before ship date) in merged_detail, not
    # a genuine gap, but the logic itself is correct to have regardless.
    #
    # When port_eta is missing, the arrival state is genuinely unknown —
    # NOT assumed "still on water" just because status isn't "At branch"
    # either, which would repeat the exact mistake this logic exists to
    # avoid. Verified: 20 SIPLs have no port_eta (and, per the lockstep
    # finding above, no location_eta either).
    def _physical_status(row):
        # Check definitive arrival signals FIRST, before the sailing check.
        # "At branch" (or a passed location_eta) is unambiguous evidence the
        # SIPL has arrived — and arrival implies it sailed — even if this
        # specific SIPL row has no Inventory Detail match to confirm
        # ship_b_l_date directly. Previously this ordering was reversed, so
        # a SIPL with sipl_status=="At branch" but no inventory match was
        # wrongly reported "Cannot Determine (No Detail)" instead of
        # "Delivered (At Branch)" — confirmed real instances: SIPLs
        # 159840C, 159842C, 159843C, each sharing a container with a
        # sibling SIPL already confirmed "At branch" at an identical port_eta.
        if row["sipl_status"] == "At branch":
            return "Delivered (At Branch)"
        if pd.notna(row["location_eta"]) and row["location_eta"] < today:
            return "Delivered (At Branch)"
        if row["sailing_status"] == "Cannot Determine (No Detail)":
            return "Cannot Determine (No Detail)"
        if row["sailing_status"] == "Not Yet Sailed":
            return "Not Yet Sailed"
        if pd.isna(row["port_eta"]):
            return "Cannot Determine (No ETA Data)"
        return "Arrived, Processing" if row["port_eta"] < today else "On the Water"

    sipl_summary["physical_status"] = sipl_summary.apply(_physical_status, axis=1)
    sipl_summary["is_currently_on_water"] = sipl_summary["physical_status"] == "On the Water"

    # container_consolidation: grouped from sipl_clean (the full
    # container-tracked universe, 225 rows), not merged_detail, so
    # containers with no inventory detail still appear. Reuses routing_check
    # computed above rather than recomputing ship_to/departure_port nunique.
    consolidation = routing_check.reset_index()
    consolidation["sipl_count"] = consolidation["container"].map(sipl_clean.groupby("container")["sipl"].nunique())
    consolidation = consolidation.merge(
        sipl_summary.groupby("container")["total_value"].sum().rename("total_value"),
        on="container", how="left"
    )
    consolidation["total_value"] = consolidation["total_value"].fillna(0)

    # Container-level physical_status — the actual "on the water" count
    # this dashboard should lead with (containers, not SIPL bookings).
    # Agreement is VERIFIED per container, not assumed from
    # is_routing_consistent — that flag only checks ship_to/departure_port
    # match, which turned out NOT to guarantee sibling SIPLs share the same
    # ship_b_l_date/port_eta. Confirmed against real data: 2 containers
    # (MEDU5655981, MSDU8402053) are flagged is_routing_consistent=True
    # (same ship_to/departure_port) yet have siblings with genuinely
    # DIFFERENT non-null ship_b_l_date values a week+ apart — propagation
    # correctly leaves these alone (fillna only fills missing values, never
    # overwrites a real conflicting one), so their physical_status still
    # legitimately disagrees after the fill. For any container where
    # sibling SIPLs still disagree post-propagation — for this reason or
    # genuine reuse — the most-recently-active SIPL's status is reported as
    # primary and has_mixed_status is flagged explicitly, rather than
    # silently picking one status without disclosure.
    def _container_status(container_id):
        rows = sipl_summary[sipl_summary["container"] == container_id]
        if rows["physical_status"].nunique() == 1:
            return rows["physical_status"].iloc[0], False
        most_recent = rows.sort_values("ship_b_l_date", ascending=False, na_position="last").iloc[0]
        return most_recent["physical_status"], True

    status_results = consolidation["container"].apply(_container_status)
    consolidation["physical_status"] = status_results.apply(lambda x: x[0])
    consolidation["has_mixed_status"] = status_results.apply(lambda x: x[1])

    # Container's own (propagated) dates, LFD, and location fields, for
    # direct container-level display without re-deriving from sipl_summary
    # every time a dashboard view needs them. ship_to_location and
    # departure_port take the first non-null value per container — exact
    # for routing-consistent containers (nunique <= 1 by definition), a
    # "primary" pick for the rare inconsistent one (same treatment as
    # physical_status's most-recent-wins rule, consistent within this file).
    consolidation = consolidation.merge(
        sipl_summary.groupby("container").agg(
            ship_b_l_date=("ship_b_l_date", "min"),
            port_eta=("port_eta", "min"),
            location_eta=("location_eta", "min"),
            lfd=("lfd", "min"),
            ship_to_location=("ship_to_location", "first"),
            departure_port=("departure_port", "first"),
        ),
        on="container", how="left"
    )
    consolidation["has_lfd"] = consolidation["lfd"].notna()

    # Vendors/suppliers aboard a container can legitimately differ even for
    # a single, routing-consistent voyage (multiple suppliers' goods
    # consolidated into one container) — reported as a delimiter-joined list
    # (" | ", not ",": confirmed in the Bills/GL work that vendor names
    # themselves can contain commas), not assumed to be one value.
    consolidation = consolidation.merge(
        sipl_summary.groupby("container")["supplier"]
        .apply(lambda s: " | ".join(sorted(set(s.dropna().astype(str)))))
        .rename("suppliers"),
        on="container", how="left"
    )

    # Container-level transit time: ship_b_l_date -> port_eta (both already
    # container-level, propagated fields above) — NOT re-derived from
    # merged_detail's line-item eta_date, which would count a container once
    # per product line it carries instead of once. has_transit_anomaly flags
    # a negative value (port_eta before ship_b_l_date — always a data error,
    # never a real transit time).
    consolidation["transit_days"] = (consolidation["port_eta"] - consolidation["ship_b_l_date"]).dt.days
    consolidation["has_transit_anomaly"] = consolidation["transit_days"] < 0
    consolidation["departure_country"] = extract_country_from_port(consolidation["departure_port"])

    return {
        "merged_detail": merged_detail,
        "sipl_summary": sipl_summary,
        "container_consolidation": consolidation,
    }


def get_shipment_container_detail(container_id, sipl_summary, container_consolidation):
    """
    Look up full detail for a single container within the SIPL tracking +
    Inventory Detail merge — the container-level lookup for
    shipment_insights.py, matching the same shape/spirit as
    get_container_detail() (the Bills+GL container search) elsewhere in
    this file.

    Args:
        container_id: exact container ID to look up (caller should
                       uppercase/strip user input first)
        sipl_summary: merge_sipl_inventory()['sipl_summary']
        container_consolidation: merge_sipl_inventory()['container_consolidation']

    Returns:
        dict with keys:
          - 'found': bool
          - 'physical_status', 'has_mixed_status', 'sipl_count', 'total_value',
            'is_routing_consistent', 'ship_b_l_date', 'port_eta', 'location_eta'
            — the container-level fields from container_consolidation
          - 'sipls': DataFrame, one row per SIPL aboard this container
            (drill-down detail — physical_status, dates, value, sipl_status,
            supplier, ship_to per SIPL), sorted most-recently-active first
    """
    container_row = container_consolidation[container_consolidation["container"] == container_id]
    if len(container_row) == 0:
        return {"found": False, "container": container_id}

    row = container_row.iloc[0]
    sipls = sipl_summary[sipl_summary["container"] == container_id][
        ["sipl", "physical_status", "ship_b_l_date", "port_eta", "location_eta",
         "total_value", "sipl_status", "supplier", "ship_to_location"]
    ].sort_values("ship_b_l_date", ascending=False, na_position="last")

    return {
        "found": True,
        "container": container_id,
        "physical_status": row["physical_status"],
        "has_mixed_status": bool(row["has_mixed_status"]),
        "sipl_count": int(row["sipl_count"]),
        "total_value": row["total_value"],
        "is_routing_consistent": bool(row["is_routing_consistent"]),
        "ship_b_l_date": row["ship_b_l_date"],
        "port_eta": row["port_eta"],
        "location_eta": row["location_eta"],
        "sipls": sipls,
    }
