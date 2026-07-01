# -*- coding: utf-8 -*-
"""
build_dashboard_data.py
Architectural Surfaces — Logistics Dashboard Builder
Author  : Abhay

PURPOSE:
    Replaces the two-step ETL_Clean_Load.py → generate_dashboard_data.py pipeline.
    Reads all source files DIRECTLY (no SQL round-trip), cleans them in-memory,
    builds the invoice compliance matrix, and writes a single dashboard_data.xlsx
    that powers app_cloud.py.

    SQL is bypassed entirely — this preserves fields like ship_b_l_date that
    were being silently dropped or corrupted in the SQL load/read cycle.

RUN:
    python build_dashboard_data.py

OUTPUTS:
    dashboard_data_{date}.xlsx  — dated archive
    dashboard_data.xlsx         — latest (overwritten each run)

SHEETS WRITTEN:
    bookings              — cleaned bookings tracker
    shipment_mapping      — container ↔ PO ↔ SIPL mapping
    open_po               — open PO list
    in_transit            — SIPL in-transit list
    inventory_intransit   — inventory in-transit detail (includes ship_b_l_date)
    bills                 — SPS bills
    gl_bills              — GL account registers (1275 + 1313)
    invoice_compliance    — per-container bill compliance matrix (OF/CUSTOMS/DUTY/DRAYAGE)
    qc_snapshot           — QC audit snapshot for this run
"""

import re
import numpy as np
import pandas as pd
from lxml import etree
from pathlib import Path
from datetime import datetime

# =============================================================================
# FILE PATHS  — adjust if folder locations change
# =============================================================================

TEAM_FILE_PATH  = Path(r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Freight Bills Processed - 2026.xlsm")
BOOKINGS_PATH   = Path(r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Bookings Tracker.xlsx")
BASE_PATH       = Path(r"C:\Users\Abhay\OneDrive - Architectural Surfaces\Desktop\Logistics dashboard")

OUTPUT_FOLDER   = Path(
    r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics"
    r"\Logistics Tracker\Dashboards\Container Movement Control Tower"
)

# =============================================================================
# OUTPUT PATHS
# =============================================================================

OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

today_str    = datetime.now().strftime("%Y-%m-%d")
dated_file   = OUTPUT_FOLDER / f"dashboard_data_{today_str}.xlsx"
latest_file  = OUTPUT_FOLDER / "dashboard_data.xlsx"

# =============================================================================
# SHARED UTILITIES
# =============================================================================

def load_html_table(path: Path, table_index: int = 0) -> pd.DataFrame:
    parser = etree.HTMLParser(recover=True, encoding="utf-8")
    with open(path, "rb") as f:
        tree = etree.parse(f, parser)
    tables = tree.xpath("//table")
    if table_index >= len(tables):
        raise ValueError(f"Table index {table_index} not found in {path.name}")
    table = tables[table_index]
    rows = []
    for tr in table.xpath(".//tr"):
        cells = tr.xpath("./th|./td")
        row = ["".join(c.itertext()).strip() for c in cells]
        if any(row):
            rows.append(row)
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    header_idx = 0
    for i, row in enumerate(rows):
        non_empty = sum(1 for c in row if c)
        if len(row) > 1 and non_empty / len(row) >= 0.5:
            header_idx = i
            break
    df = pd.DataFrame(rows[header_idx + 1:], columns=rows[header_idx])
    return df.reset_index(drop=True)


def standardize_columns(df):
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )
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
    return pd.to_datetime(series, errors="coerce").dt.normalize()


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
    return cleaned.replace({"": np.nan, "nan": np.nan, "None": np.nan})


def clean_container_strict(val):
    if pd.isna(val) or str(val).strip() == "":
        return None
    val = str(val).upper().strip()
    for pat in ["TRUCK", "L&S", "R&L"]:
        if pat in val:
            return None
    match = re.search(r"\b[A-Z]{4}[0-9]{7}\b", val)
    return match.group(0) if match else None


def clean_container(val):
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    val = str(val).upper().strip()
    if "AIR FREIGHT" in val:
        return "AIR FREIGHT"
    for pat in ["TRUCK", "L&S", "R&L"]:
        if pat in val:
            return None
    match = re.search(r"\b[A-Z]{4}[0-9]{7}\b", val)
    return match.group(0) if match else None


# =============================================================================
# STEP 1 — LOAD ALL SOURCE FILES
# =============================================================================

print("=" * 60)
print("  STEP 1: LOADING SOURCE FILES")
print("=" * 60)

KEEP_COLS = ['S No', 'Date Processed', 'SIPL', 'Container', 'SPS inv NO',
             'Invoice Date', 'GL Posted', 'Invoice Type', 'AMOUNT']

freight_bills_bhargava = pd.read_excel(TEAM_FILE_PATH, sheet_name="Bhargava", engine="openpyxl")
freight_bills_sanket   = pd.read_excel(TEAM_FILE_PATH, sheet_name="Sanket",   engine="openpyxl")
bookings_raw           = pd.read_excel(BOOKINGS_PATH)

account_1275_raw        = load_html_table(BASE_PATH / "Account Register_ 1275 - Capitalized Inventory Freight.xls")
account_1313_raw        = load_html_table(BASE_PATH / "Account Register_ 1313 - Prepaid Container Freight.xls")
bills_raw               = load_html_table(BASE_PATH / "Bills.xls")
in_transit_raw          = load_html_table(BASE_PATH / "In-Transit List by SIPL.xls")
open_po_raw             = load_html_table(BASE_PATH / "Open PO List.xls")
inventory_intransit_raw = load_html_table(BASE_PATH / "Inventory In Transit - Detail .xls")
inventory_received_raw  = load_html_table(BASE_PATH / "Inventory  Received.xls")
shipment_mapping_raw    = load_html_table(BASE_PATH / "Supplier Invoices.xls")

print("✅ All source files loaded")

# =============================================================================
# STEP 2 — CLEAN ALL DATAFRAMES
# =============================================================================

print("\n" + "=" * 60)
print("  STEP 2: CLEANING ALL DATAFRAMES")
print("=" * 60)

# -----------------------------------------------------------------------------
# FREIGHT BILLS
# -----------------------------------------------------------------------------

KEY_COLS = ['s_no', 'date_processed', 'sipl', 'container', 'sps_inv_no',
            'invoice_date', 'gl_posted', 'invoice_type', 'amount']

freight_bills_bhargava.columns = freight_bills_bhargava.columns.astype(str).str.strip()
freight_bills_sanket.columns   = freight_bills_sanket.columns.astype(str).str.strip()

freight_bills_bhargava = standardize_columns(freight_bills_bhargava[KEEP_COLS])
freight_bills_sanket   = standardize_columns(freight_bills_sanket[KEEP_COLS])

freight_bills_bhargava = freight_bills_bhargava.dropna(subset=KEY_COLS, how="all")
freight_bills_sanket   = freight_bills_sanket.dropna(subset=KEY_COLS, how="all")

freight_bills_bhargava["input_by"] = "Bhargava"
freight_bills_sanket["input_by"]   = "Sanket"

freight_bills = pd.concat([freight_bills_bhargava, freight_bills_sanket], ignore_index=True)

def clean_freight_bills(df):
    df = df.copy()
    df["s_no"]           = df["s_no"].pipe(clean_numeric).astype("Int64")
    df["amount"]         = df["amount"].pipe(clean_currency)
    df["gl_posted"]      = df["gl_posted"].pipe(clean_numeric)
    df["date_processed"] = df["date_processed"].pipe(clean_date)
    df["invoice_date"]   = df["invoice_date"].pipe(clean_date)
    df["sipl"]           = df["sipl"].pipe(clean_text)
    df["sps_inv_no"]     = df["sps_inv_no"].pipe(clean_text).str.upper()
    df["invoice_type"]   = df["invoice_type"].pipe(clean_text).str.title()
    df["input_by"]       = df["input_by"].pipe(clean_text)
    return df.reset_index(drop=True)

freight_bills = clean_freight_bills(freight_bills)
print(f"  freight_bills      : {freight_bills.shape}")

# -----------------------------------------------------------------------------
# GL BILLS (accounts 1275 + 1313)
# -----------------------------------------------------------------------------

def clean_gl_bills(df):
    df = df.copy()
    df = df.drop(columns=["division", "reconciled"], errors="ignore")
    df = df[df["date"].astype(str).str.upper().str.strip() != "BALANCE FORWARD"]
    df["date"]        = df["date"].pipe(clean_date)
    df["debit"]       = df["debit"].pipe(clean_currency)
    df["credit"]      = df["credit"].pipe(clean_currency)
    df["balance"]     = df["balance"].pipe(clean_currency)
    df["invoice"]     = df["invoice"].pipe(clean_text).str.upper()
    df["transaction"] = df["transaction"].pipe(clean_text)
    df["description"] = df["description"].pipe(clean_text).str.upper()
    df["party"]       = df["party"].pipe(clean_text)
    df["type"]        = df["type"].pipe(clean_text).str.title()
    if "location" in df.columns:
        df["location"] = df["location"].pipe(clean_text).str.upper()
    return df.reset_index(drop=True)

account_1275 = account_1275_raw.drop(index=0).reset_index(drop=True)
account_1313 = account_1313_raw.drop(index=0).reset_index(drop=True)
account_1275 = standardize_columns(account_1275)
account_1313 = standardize_columns(account_1313)
account_1275["gl_account"] = 1275
account_1313["gl_account"] = 1313

gl_bills = pd.concat([account_1275, account_1313], ignore_index=True)
gl_bills = clean_gl_bills(gl_bills)
print(f"  gl_bills           : {gl_bills.shape}")

# -----------------------------------------------------------------------------
# BILLS
# -----------------------------------------------------------------------------

def clean_bills(df):
    df = df.copy()
    df = df.drop(columns=["created_by"], errors="ignore")
    for col in ["invoice_dt", "due_date", "sipl_inv_dt", "sipl_ship_dt"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    for col in ["amount", "balance_due", "sipl_amount"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_currency)
    df["bill_inv"]             = df["bill_inv"].pipe(clean_text).str.upper()
    df["non_inventory_vendor"] = df["non_inventory_vendor"].pipe(clean_text)
    df["supplier"]             = df["supplier"].pipe(clean_text)
    df["sipl_inv"]             = df["sipl_inv"].pipe(clean_text)
    df["notes"]                = df["notes"].pipe(clean_text)
    return df.reset_index(drop=True)

bills = bills_raw.iloc[:, :14].copy()
bills = standardize_columns(bills)
if len(bills.columns) == 14:
    bills.columns = list(bills.columns[:-1]) + ["notes"]
bills["container"] = bills["container"].apply(clean_container)
bills = bills[bills["container"].notna() | bills["notes"].notna()].reset_index(drop=True)
bills = clean_bills(bills)
print(f"  bills              : {bills.shape}")

# -----------------------------------------------------------------------------
# IN_TRANSIT
# -----------------------------------------------------------------------------

def clean_in_transit(df):
    df = df.copy()
    df = df.replace({"Donotshow": np.nan, "donotshow": np.nan})
    if "initiated_on" in df.columns:
        df["initiated_on"] = (
            df["initiated_on"].astype(str)
            .str.replace("Initiated On", "", case=False)
            .str.strip()
        )
    for col in ["port_eta", "rail_eta", "location_eta", "lfd", "eta_date", "initiated_on"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    for col in ["sipl", "supplier", "ship_to_location", "purchase_location",
                "vessel", "sipl_status", "status", "fr_forwarder", "departure_port"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    df = df.drop(columns=["rail_eta"], errors="ignore")
    return df.reset_index(drop=True)

in_transit = standardize_columns(in_transit_raw)
in_transit["container"] = in_transit["container"].apply(clean_container_strict)
in_transit = in_transit[in_transit["container"].notna()].reset_index(drop=True)
in_transit = clean_in_transit(in_transit)
print(f"  in_transit         : {in_transit.shape}")

# -----------------------------------------------------------------------------
# OPEN_PO
# -----------------------------------------------------------------------------

def clean_open_po(df):
    df = df.copy()
    for col in ["po_date", "req_ship_date", "planned_ex_factory_date",
                "etd_port", "booked_eta_port", "eta_port", "location_eta"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    df["quantity"]  = df["quantity"].pipe(clean_numeric)
    df["slabs"]     = df["slabs"].pipe(clean_numeric)
    df["unitcost"]  = df["unitcost"].pipe(clean_currency)
    df["totalcost"] = df["totalcost"].pipe(clean_currency)
    for col in ["po", "sup_so", "origin", "ship_to_location", "purchase_location",
                "product", "type", "category", "supplier", "uom",
                "po_status", "vessel", "freight_forwarder"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    return df.reset_index(drop=True)

open_po = standardize_columns(open_po_raw)
open_po["container"] = open_po["container"].apply(clean_container_strict)
open_po = clean_open_po(open_po)
print(f"  open_po            : {open_po.shape}")

# -----------------------------------------------------------------------------
# INVENTORY_INTRANSIT
# NOTE: ship_b_l_date is cleaned here directly from the source file.
#       This field was silently lost in the previous SQL round-trip.
# -----------------------------------------------------------------------------

def clean_inventory_intransit(df):
    df = df.copy()
    # ship_b_l_date explicitly included — this is the field that was missing
    for col in ["sipl_date", "req_ship_date", "ship_b_l_date", "eta_date"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    df["quantity"]   = df["quantity"].pipe(clean_numeric)
    df["slabs"]      = df["slabs"].pipe(clean_numeric)
    df["unit_cost"]  = df["unit_cost"].pipe(clean_currency)
    df["total_cost"] = df["total_cost"].pipe(clean_currency)
    for col in ["name", "sku", "type", "category", "subcategory", "group",
                "supplier", "sipl", "freight_forwarder", "departure_port",
                "arrival_port", "units", "bill_to", "ship_to"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    return df.reset_index(drop=True)

inventory_intransit = standardize_columns(inventory_intransit_raw)
inventory_intransit["container"] = inventory_intransit["container"].apply(clean_container_strict)
inventory_intransit = inventory_intransit[
    inventory_intransit["container"].notna()
].reset_index(drop=True)
inventory_intransit = clean_inventory_intransit(inventory_intransit)
print(f"  inventory_intransit: {inventory_intransit.shape}")

# Check ship_b_l_date coverage
if "ship_b_l_date" in inventory_intransit.columns:
    non_null = inventory_intransit["ship_b_l_date"].notna().sum()
    print(f"    ship_b_l_date non-null: {non_null:,} / {len(inventory_intransit):,}")
else:
    print("    WARNING: ship_b_l_date column not found in source")

# -----------------------------------------------------------------------------
# BOOKINGS
# -----------------------------------------------------------------------------

def clean_bookings(df):
    df = df.copy()
    df = df.drop(columns=["notes"], errors="ignore")
    df["event_date"] = df["event_date"].pipe(clean_date)
    df["etd"]        = df["etd"].pipe(clean_date)
    df["eta"]        = df["eta"].pipe(clean_date)
    df["event_id"]   = df["event_id"].pipe(clean_numeric).astype("Int64")
    df["po_number"]  = df["po_number"].pipe(clean_numeric).astype("Int64")
    for col in ["container_id", "vessel", "event_status", "reason", "updated_by"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    return df.reset_index(drop=True)

bookings = standardize_columns(bookings_raw)
bookings = clean_bookings(bookings)
print(f"  bookings           : {bookings.shape}")

# -----------------------------------------------------------------------------
# INVENTORY_RECEIVED
# -----------------------------------------------------------------------------

def clean_inventory_received(df):
    df = df.copy()
    for col in ["po_date", "sipl_date", "ship_b_l_date", "rec_date", "inv_date"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    for col in ["fob_unit_cost", "fob_total_cost", "landed_unit_cost", "landed_total_cost"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_currency)
    df["quantity"] = df["quantity"].pipe(clean_numeric)
    df["slabs"]    = df["slabs"].pipe(clean_numeric)
    return df.reset_index(drop=True)

inventory_received = standardize_columns(inventory_received_raw)
inventory_received["container"] = inventory_received["container"].apply(clean_container_strict)
inventory_received = inventory_received[
    inventory_received["container"].notna()
].reset_index(drop=True)
inventory_received = clean_inventory_received(inventory_received)
print(f"  inventory_received : {inventory_received.shape}")

# -----------------------------------------------------------------------------
# SHIPMENT_MAPPING
# -----------------------------------------------------------------------------

def clean_shipment_mapping(df):
    df = df.copy()
    df["po_number"]   = df["p_o"].pipe(clean_text)
    df["sipl_number"] = df["sipl"].pipe(clean_text)
    df["container_id"] = df["container"].apply(clean_container_strict)
    df = df[df["container_id"].notna()].copy()
    df = df.drop_duplicates().reset_index(drop=True)
    return df

shipment_mapping = standardize_columns(shipment_mapping_raw)
shipment_mapping = shipment_mapping.loc[:, shipment_mapping.columns != ""]
shipment_mapping = clean_shipment_mapping(shipment_mapping)
print(f"  shipment_mapping   : {shipment_mapping.shape}")

print("\n✅ All dataframes cleaned")

# =============================================================================
# STEP 3 — BUILD INVOICE COMPLIANCE MATRIX
# Runs entirely on in-memory DataFrames — no SQL needed.
# Logic mirrors build_invoice_compliance() in logic.py.
# =============================================================================

print("\n" + "=" * 60)
print("  STEP 3: BUILDING INVOICE COMPLIANCE MATRIX")
print("=" * 60)

invoice_compliance = pd.DataFrame()

try:
    from rapidfuzz import process, fuzz

    # -------------------------------------------------------------------------
    # BUILD SIPL ↔ CONTAINER MASTER FROM BILLS
    # -------------------------------------------------------------------------
    master = (
        bills[bills["sipl_inv"].notna() & bills["container"].notna()]
        [["sipl_inv", "container"]]
        .drop_duplicates()
    )

    sipl_set      = set(master["sipl_inv"].astype(str).str.upper())
    container_set = set(master["container"].astype(str).str.upper())

    # -------------------------------------------------------------------------
    # EXTRACT SIPL / CONTAINER FROM NOTES (FALLBACK)
    # -------------------------------------------------------------------------
    def extract_sipl(note):
        if pd.isna(note):
            return None
        candidates = re.findall(r"(\d{5,6}[A-Z]?)", str(note).upper())
        matches = [x for x in candidates if x in sipl_set]
        return matches[0] if matches else None

    def extract_container(note):
        if pd.isna(note):
            return None
        candidates = re.findall(r"([A-Z]{4}\d{7})", str(note).upper())
        matches = [x for x in candidates if x in container_set]
        return matches[0] if matches else None

    bills_wk = bills.copy()
    bills_wk["sipl_from_notes"]      = bills_wk["notes"].apply(extract_sipl)
    bills_wk["container_from_notes"] = bills_wk["notes"].apply(extract_container)

    # Coalesce sipl
    bills_wk["sipl_final"] = bills_wk["sipl_inv"]
    mask = bills_wk["sipl_final"].isna() & bills_wk["sipl_from_notes"].notna()
    bills_wk.loc[mask, "sipl_final"] = bills_wk.loc[mask, "sipl_from_notes"]

    # Coalesce container
    bills_wk["container_final"] = bills_wk["container"]
    mask = bills_wk["container_final"].isna() & bills_wk["container_from_notes"].notna()
    bills_wk.loc[mask, "container_final"] = bills_wk.loc[mask, "container_from_notes"]

    # Fill container from SIPL master
    container_map = master.drop_duplicates("sipl_inv").set_index("sipl_inv")["container"]
    bills_wk["container_from_sipl"] = bills_wk["sipl_final"].map(container_map)
    mask = bills_wk["container_final"].isna() & bills_wk["container_from_sipl"].notna()
    bills_wk.loc[mask, "container_final"] = bills_wk.loc[mask, "container_from_sipl"]

    # -------------------------------------------------------------------------
    # KEEP CONTAINER-LINKED BILLS ONLY
    # -------------------------------------------------------------------------
    container_bills = bills_wk[bills_wk["container_final"].notna()].copy()
    container_bills = container_bills.drop(
        columns=["supplier", "sipl_inv", "container", "notes",
                 "sipl_from_notes", "container_from_notes", "container_from_sipl"],
        errors="ignore"
    )
    container_bills = container_bills.rename(
        columns={"sipl_final": "sipl", "container_final": "container"}
    )

    # -------------------------------------------------------------------------
    # FILTER GL TO BILL TYPE ONLY + CLEAN DESCRIPTION
    # -------------------------------------------------------------------------
    gl_wk = gl_bills[gl_bills["type"] == "Bill"].copy()
    gl_wk["description_clean"] = gl_wk["description"].astype(str).str.upper().str.strip()

    # Remove PO references
    gl_wk.loc[
        gl_wk["description_clean"].str.contains(r"PO", case=False, na=False),
        "description_clean"
    ] = pd.NA

    gl_wk["description_clean"] = gl_wk["description_clean"].replace({
        "OCEAN FREIGHT": "OF",
        "AIR FREIGHT": "OF",
        "MIS": "MISC"
    })
    gl_wk = gl_wk[gl_wk["description_clean"].notna()].copy()

    # -------------------------------------------------------------------------
    # MERGE BILLS → GL
    # -------------------------------------------------------------------------
    matched = container_bills.merge(
        gl_wk,
        left_on="bill_inv",
        right_on="invoice",
        how="left"
    )

    # -------------------------------------------------------------------------
    # MERGE → PO NUMBER (FROM SHIPMENT MAPPING)
    # -------------------------------------------------------------------------
    sm = shipment_mapping.rename(
        columns={"container_id": "container", "sipl_number": "sipl"}
    )[["container", "sipl", "po_number"]].drop_duplicates()
    sm["container"] = sm["container"].astype(str).str.upper().str.strip()
    sm["sipl"]      = sm["sipl"].astype(str).str.upper().str.strip()

    matched = matched.merge(sm, on=["container", "sipl"], how="left")

    # -------------------------------------------------------------------------
    # NORMALIZE BILL TYPES  (all 4 categories: OF / CUSTOMS / DUTY / DRAYAGE)
    # -------------------------------------------------------------------------
    VALID_TYPES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]

    def normalize_bill_type(text):
        text = str(text).strip().upper() if pd.notna(text) else ""
        if text == "":
            return None
        if text == "OF" or "OCEAN" in text or "AIR FREIGHT" in text or "AIRFREIGHT" in text:
            return "OF"
        if "CUSTOM" in text:
            return "CUSTOMS"
        if "DUTY" in text:
            return "DUTY"
        if "DRAY" in text:
            return "DRAYAGE"
        # Fuzzy fallback for typos
        match = process.extractOne(text, VALID_TYPES, scorer=fuzz.ratio)
        if match and match[1] >= 85:
            return match[0]
        return None

    matched["bill_type"] = matched["description_clean"].apply(normalize_bill_type)

    compliance_bills = matched[matched["bill_type"].notna()].copy()

    # -------------------------------------------------------------------------
    # BUILD COMPLIANCE MATRIX  (pivot: container × PO → which bills exist)
    # -------------------------------------------------------------------------
    REQUIRED = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]

    invoice_matrix = (
        compliance_bills
        .drop_duplicates(subset=["container", "po_number", "bill_type"])
        .assign(flag=1)
        .pivot_table(
            index=["container", "po_number"],
            columns="bill_type",
            values="flag",
            fill_value=0
        )
        .reset_index()
    )

    # Ensure all 4 columns exist even if no bills of that type found
    for bill in REQUIRED:
        if bill not in invoice_matrix.columns:
            invoice_matrix[bill] = 0

    def get_missing(row):
        return ", ".join(b for b in REQUIRED if row[b] == 0)

    invoice_matrix["missing_bills"]  = invoice_matrix.apply(get_missing, axis=1)
    invoice_matrix["invoice_ready"]  = invoice_matrix["missing_bills"] == ""

    invoice_compliance = invoice_matrix
    print(f"  invoice_compliance : {invoice_compliance.shape}")
    print(f"  Containers covered : {invoice_compliance['container'].nunique():,}")
    print(f"  Fully compliant    : {invoice_compliance['invoice_ready'].sum():,}")
    for b in REQUIRED:
        missing_count = (invoice_compliance[b] == 0).sum()
        print(f"  Missing {b:<8}: {missing_count:,}")

except ImportError:
    print("  WARNING: rapidfuzz not installed — invoice_compliance sheet will be empty.")
    print("           Run: pip install rapidfuzz")
except Exception as e:
    print(f"  WARNING: Invoice compliance build failed — sheet will be empty.\n  {e}")

# =============================================================================
# STEP 4 — QC SNAPSHOT  (replaces SQL append — written as Excel sheet instead)
# =============================================================================

print("\n" + "=" * 60)
print("  STEP 4: QC SNAPSHOT")
print("=" * 60)

PENDING_RE_QC = re.compile(r"^(PENDING|PENDNG|PENDIG|PENDIN|PEND|PENING|POSTED)$", re.IGNORECASE)
CM_RE_QC      = re.compile(r"\bCM$", re.IGNORECASE)
JUNK_RE_QC    = re.compile(r"^\d{1,4}$")
run_date      = pd.Timestamp.today().normalize()

def norm_key(val):
    if pd.isna(val):
        return ""
    return re.sub(r"[\s\xa0]+", " ", str(val).upper().strip())

# Prepare team — drop PENDING rows
team_qc = freight_bills.copy()
team_qc["invoice_key"] = team_qc["sps_inv_no"].apply(norm_key)
team_qc = team_qc[
    ~team_qc["invoice_key"].str.contains(r"^PEND", case=False, na=False)
].copy()

# Prepare bills lookup
bills_qc = bills.copy()
bills_qc = bills_qc[bills_qc["non_inventory_vendor"].notna()]
bills_qc["invoice_dt"] = pd.to_datetime(bills_qc["invoice_dt"], errors="coerce")
BILLS_START_DATE = pd.Timestamp("2025-12-20")
bills_qc = bills_qc[bills_qc["invoice_dt"] >= BILLS_START_DATE]
bills_qc["invoice_key"] = bills_qc["bill_inv"].apply(norm_key)
bills_qc = bills_qc[
    ~bills_qc["invoice_key"].str.contains(r"^PEND", case=False, na=False)
].copy()

team_qc = team_qc.merge(
    bills_qc[["invoice_key", "amount", "container", "invoice_dt", "non_inventory_vendor"]]
    .rename(columns={
        "amount": "sps_amount",
        "container": "sps_container",
        "invoice_dt": "sps_invoice_dt",
        "non_inventory_vendor": "sps_vendor",
    }),
    on="invoice_key",
    how="left"
)

errors = []

for _, row in team_qc.iterrows():
    inv    = row["invoice_key"]
    person = row.get("input_by", "Unknown")
    base   = {
        "run_date"     : run_date,
        "invoice"      : inv,
        "input_by"     : person,
        "date_processed": row.get("date_processed"),
        "sipl"         : row.get("sipl"),
        "container"    : row.get("container"),
        "amount"       : row.get("amount"),
        "gl_posted"    : row.get("gl_posted"),
        "invoice_date" : row.get("invoice_date"),
    }

    if bool(CM_RE_QC.search(str(inv))) or bool(JUNK_RE_QC.match(str(inv))) or inv == "":
        continue

    raw_inv = str(row.get("sps_inv_no", ""))
    if re.search(r"  ", raw_inv):
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_DOUBLE_SPACE",
                       "error_detail": f"Double space: '{raw_inv}'"})
    if "\t" in raw_inv:
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_TAB_CHAR",
                       "error_detail": f"Tab character: '{raw_inv}'"})
    if re.search(r"\?", raw_inv):
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_TYPO_CHAR",
                       "error_detail": f"Unexpected character: '{raw_inv}'"})

    if pd.isna(row.get("sps_amount")):
        errors.append({**base, "error_type": "CRITICAL", "error_code": "NOT_IN_SPS",
                       "error_detail": "Invoice not found in SPS bills (2026)"})
        continue

    if (team_qc["invoice_key"] == inv).sum() > 1:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "DUPLICATE_INVOICE",
                       "error_detail": f"Invoice appears {(team_qc['invoice_key'] == inv).sum()} times"})

    if pd.isna(row.get("container")) or str(row.get("container", "")).strip() == "":
        errors.append({**base, "error_type": "CRITICAL", "error_code": "MISSING_CONTAINER",
                       "error_detail": "Container not recorded in team tracker"})

    if pd.isna(row.get("amount")):
        errors.append({**base, "error_type": "CRITICAL", "error_code": "AMOUNT_NULL",
                       "error_detail": "Amount is missing in team tracker"})

    team_cont = str(row.get("container", "")).strip().upper()
    sps_cont  = str(row.get("sps_container", "")).strip().upper()
    if team_cont and sps_cont and team_cont not in ("NAN", "") and \
       sps_cont not in ("NAN", "") and team_cont != sps_cont:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "CONTAINER_MISMATCH",
                       "error_detail": f"Team: '{team_cont}' | SPS: '{sps_cont}'"})

    team_amt = row.get("amount")
    sps_amt  = row.get("sps_amount")
    if pd.notna(team_amt) and pd.notna(sps_amt):
        diff = abs(float(team_amt) - float(sps_amt))
        if diff > 1.00:
            errors.append({**base, "error_type": "CRITICAL", "error_code": "AMOUNT_MISMATCH",
                           "error_detail": f"Team: ${float(team_amt):,.2f} | SPS: ${float(sps_amt):,.2f} | Diff: ${diff:,.2f}"})

    team_gl = row.get("gl_posted")
    if pd.notna(team_gl) and int(team_gl) not in {1275, 1313}:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "INVALID_GL_ACCOUNT",
                       "error_detail": f"GL account '{int(team_gl)}' not in approved list"})

    team_dt = row.get("invoice_date")
    sps_dt  = row.get("sps_invoice_dt")
    if pd.notna(team_dt) and pd.notna(sps_dt):
        if pd.Timestamp(team_dt).normalize() != pd.Timestamp(sps_dt).normalize():
            errors.append({**base, "error_type": "STANDARD", "error_code": "DATE_MISMATCH",
                           "error_detail": f"Team: {str(team_dt)[:10]} | SPS: {str(sps_dt)[:10]}"})

if errors:
    qc_snapshot = pd.DataFrame(errors)
    qc_snapshot["run_date"] = pd.to_datetime(qc_snapshot["run_date"])
    print(f"  QC errors found  : {len(qc_snapshot):,}")
    print(f"  Critical         : {(qc_snapshot['error_type'] == 'CRITICAL').sum():,}")
    print(f"  Standard         : {(qc_snapshot['error_type'] == 'STANDARD').sum():,}")
    print(f"\n  Error breakdown  :")
    print(qc_snapshot["error_code"].value_counts().to_string())
else:
    qc_snapshot = pd.DataFrame(columns=[
        "run_date", "invoice", "input_by", "date_processed", "sipl",
        "container", "amount", "gl_posted", "invoice_date",
        "error_type", "error_code", "error_detail"
    ])
    print("  ✅ No QC errors found in this run")

# =============================================================================
# STEP 5 — WRITE DASHBOARD_DATA.XLSX
# =============================================================================

print("\n" + "=" * 60)
print("  STEP 5: WRITING DASHBOARD FILES")
print("=" * 60)

# Sheets required by app_cloud.py
CORE_SHEETS = {
    "bookings":             bookings,
    "shipment_mapping":     shipment_mapping,
    "open_po":              open_po,
    "in_transit":           in_transit,
    "inventory_intransit":  inventory_intransit,
    "bills":                bills,
    "gl_bills":             gl_bills,
    "qc_snapshot":          qc_snapshot,
}

for file_path in [dated_file, latest_file]:

    print(f"\n  Writing {file_path.name} ...")

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:

        for sheet_name, df in CORE_SHEETS.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            print(f"    ✅ {sheet_name:<25} {len(df):>6,} rows")

        if not invoice_compliance.empty:
            invoice_compliance.to_excel(writer, sheet_name="invoice_compliance", index=False)
            print(f"    ✅ {'invoice_compliance':<25} {len(invoice_compliance):>6,} rows")
        else:
            print(f"    ⚠️  invoice_compliance skipped (build failed)")

print("\n" + "=" * 60)
print("  ✅ DASHBOARD BUILD COMPLETE")
print(f"  Archive : {dated_file}")
print(f"  Latest  : {latest_file}")
print("=" * 60)
