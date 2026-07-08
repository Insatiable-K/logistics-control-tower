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


def clean_container(val, keep_air_freight_marker=False):
    """
    Extract a clean ISO 6346 container ID from a noisy raw field.

    Returns:
        - The extracted container code (e.g. 'MEDU2304983'), or
        - 'AIR FREIGHT' if keep_air_freight_marker=True and the raw text
          indicates an air shipment (no container ID exists for air freight
          by definition — this is a valid state, not missing data), or
        - np.nan if no container ID can be determined.
    """
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    text = str(val).upper().strip()

    if keep_air_freight_marker and "AIR FREIGHT" in text:
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
    # Drop NetSuite footer artifacts (BALANCE FORWARD header rows, PAGE TOTALS
    # footer rows) that would otherwise pollute aggregations.
    first_col = df.columns[0]
    junk_mask = df[first_col].astype(str).str.upper().str.strip().isin(
        ["BALANCE FORWARD", "PAGE TOTALS", ""]
    )
    df = df[~junk_mask].reset_index(drop=True)
    return df
