# -*- coding: utf-8 -*-
"""
ETL_Clean_Load.py
Architectural Surfaces — Logistics ETL Pipeline
Author  : Abhay
Created : March 10, 2026

PURPOSE:
    1. Load all source files (Excel + HTML/XLS exports from SPS)
    2. Clean and standardize all 8 dataframes
    3. Load all 10 tables into SQL Server (logistics_db)
    4. Run QC snapshot on team data and append to qc_snapshot table
       for audit trail — errors that get corrected are still recorded

RUN:
    python ETL_Clean_Load.py

SCHEDULE:
    Windows Task Scheduler — daily
"""

import re
import urllib
import numpy as np
import pandas as pd
from lxml import etree
from pathlib import Path
from sqlalchemy import create_engine, text

# =============================================================================
# FILE PATHS
# =============================================================================

TEAM_FILE_PATH = r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Freight Bills Processed - 2026.xlsm"
BOOKINGS_PATH  = r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Bookings Tracker.xlsx"
BASE_PATH      = Path(r"C:\Users\Abhay\OneDrive - Architectural Surfaces\Desktop\Logistics dashboard")

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

    # EXCLUDE NON-CONTAINER REFERENCES
    EXCLUDE_PATTERNS = [
        "TRUCK",
        "L&S",
        "R&L"
    ]

    if any(x in val for x in EXCLUDE_PATTERNS):
        return None

    # FIND ISO CONTAINER
    match = re.search(r"\b[A-Z]{4}[0-9]{7}\b", val)

    if match:
        return match.group(0)

    return None


def clean_container(val):

    if pd.isna(val) or str(val).strip() == "":
        return np.nan

    val = str(val).upper().strip()

    # AIR FREIGHT SPECIAL CASE
    if "AIR FREIGHT" in val:
        return "AIR FREIGHT"

    # EXCLUDE NON-CONTAINER REFERENCES
    EXCLUDE_PATTERNS = [
        "TRUCK",
        "L&S",
        "R&L"
    ]

    if any(x in val for x in EXCLUDE_PATTERNS):
        return None

    # FIND ISO CONTAINER
    match = re.search(r"\b[A-Z]{4}[0-9]{7}\b", val)

    if match:
        return match.group(0)

    return None


# =============================================================================
# LOAD RAW DATA
# =============================================================================

print("=" * 60)
print("  ETL CLEAN & LOAD — LOADING SOURCE FILES")
print("=" * 60)

freight_bills_bhargava = pd.read_excel(TEAM_FILE_PATH, sheet_name="Bhargava", engine="openpyxl")
freight_bills_sanket   = pd.read_excel(TEAM_FILE_PATH, sheet_name="Sanket",   engine="openpyxl")
bookings               = pd.read_excel(BOOKINGS_PATH)

account_1275        = load_html_table(BASE_PATH / "Account Register_ 1275 - Capitalized Inventory Freight.xls")
account_1313        = load_html_table(BASE_PATH / "Account Register_ 1313 - Prepaid Container Freight.xls")
bills               = load_html_table(BASE_PATH / "Bills.xls")
in_transit          = load_html_table(BASE_PATH / "In-Transit List by SIPL.xls")
open_po             = load_html_table(BASE_PATH / "Open PO List.xls")
inventory_intransit = load_html_table(BASE_PATH / "Inventory In Transit - Detail .xls")
inventory_received  = load_html_table(BASE_PATH / "Inventory  Received.xls")
shipment_mapping    = load_html_table(BASE_PATH / "Supplier Invoices.xls")
print("✅ All source files loaded")

# =============================================================================
# FREIGHT BILLS — BHARGAVA & SANKET
# =============================================================================

KEEP_COLS = ['S No', 'Date Processed', 'SIPL', 'Container', 'SPS inv NO',
             'Invoice Date', 'GL Posted', 'Invoice Type', 'AMOUNT']
KEY_COLS  = ['s_no', 'date_processed', 'sipl', 'container', 'sps_inv_no',
             'invoice_date', 'gl_posted', 'invoice_type', 'amount']

freight_bills_bhargava.columns = freight_bills_bhargava.columns.astype(str).str.strip()
freight_bills_sanket.columns   = freight_bills_sanket.columns.astype(str).str.strip()

freight_bills_bhargava = standardize_columns(freight_bills_bhargava[KEEP_COLS])
freight_bills_sanket   = standardize_columns(freight_bills_sanket[KEEP_COLS])

freight_bills_bhargava = freight_bills_bhargava.dropna(subset=KEY_COLS, how='all')
freight_bills_sanket   = freight_bills_sanket.dropna(subset=KEY_COLS, how='all')

freight_bills_bhargava["input_by"] = "Bhargava"
freight_bills_sanket["input_by"]   = "Sanket"

freight_bills = pd.concat([freight_bills_bhargava, freight_bills_sanket], ignore_index=True)
print(f"\n=== FREIGHT BILLS === {freight_bills.shape}")

# =============================================================================
# ACCOUNT REGISTERS — 1275 & 1313
# =============================================================================

account_1275 = account_1275.drop(index=0).reset_index(drop=True)
account_1313 = account_1313.drop(index=0).reset_index(drop=True)
account_1313 = account_1313.drop(columns=["Reconciled"], errors="ignore")
account_1275 = standardize_columns(account_1275)
account_1313 = standardize_columns(account_1313)
account_1275["gl_account"] = 1275
account_1313["gl_account"] = 1313
gl_bills = pd.concat([account_1275, account_1313], ignore_index=True)
print(f"\n=== GL BILLS === {gl_bills.shape}")



# =============================================================================
# BILLS
# =============================================================================

# Keep first 14 columns (includes Notes column)
bills = bills.iloc[:, :14]

bills = standardize_columns(bills)

# Rename last column to notes
if len(bills.columns) == 14:
    bills.columns = list(bills.columns[:-1]) + ["notes"]

# Clean container field
bills["container"] = bills["container"].apply(clean_container)

# Keep rows if:
#   1. Valid container exists
#   2. Notes exist (may contain PO / SIPL / Container references)
bills = bills[
    bills["container"].notna()
    |
    bills["notes"].notna()
].reset_index(drop=True)

print(f"\n=== BILLS === {bills.shape}")
# =============================================================================
# IN_TRANSIT
# =============================================================================

in_transit = standardize_columns(in_transit)
in_transit["container"] = in_transit["container"].apply(clean_container_strict)
in_transit = in_transit[in_transit["container"].notna()].reset_index(drop=True)
print(f"\n=== IN_TRANSIT === {in_transit.shape}")

# =============================================================================
# OPEN_PO
# =============================================================================

open_po = standardize_columns(open_po)
open_po["container"] = open_po["container"].apply(clean_container_strict)
print(f"\n=== OPEN_PO === {open_po.shape}")

# =============================================================================
# INVENTORY_INTRANSIT
# =============================================================================

inventory_intransit = standardize_columns(inventory_intransit)
inventory_intransit["container"] = inventory_intransit["container"].apply(clean_container_strict)
inventory_intransit = inventory_intransit[
    inventory_intransit["container"].notna()
].reset_index(drop=True)
print(f"\n=== INVENTORY_INTRANSIT === {inventory_intransit.shape}")

# =============================================================================
# BOOKINGS
# =============================================================================

bookings = standardize_columns(bookings)
print(f"\n=== BOOKINGS === {bookings.shape}")

# =============================================================================
# INVENTORY_RECEIVED
# =============================================================================

inventory_received = standardize_columns(inventory_received)
inventory_received["container"] = inventory_received["container"].apply(clean_container_strict)
inventory_received = inventory_received[
    inventory_received["container"].notna()
].reset_index(drop=True)
print(f"\n=== INVENTORY_RECEIVED === {inventory_received.shape}")

# =============================================================================
# SHIPMENT_MAPPING — STANDARDIZE
# =============================================================================

shipment_mapping = standardize_columns(shipment_mapping)

# Drop empty column if exists
shipment_mapping = shipment_mapping.loc[:, shipment_mapping.columns != ""]

print(f"\n=== SHIPMENT_MAPPING (RAW) === {shipment_mapping.shape}")

# =============================================================================
# CLEANING FUNCTIONS
# =============================================================================

# =============================================================================
# 1. FREIGHT BILLS
# =============================================================================
# Issues found:
#   - amount         : object  → currency cleaning
#   - gl_posted      : float64 → numeric
#   - s_no           : float64 → Int64
#   - dates          : mixed   → normalize
# =============================================================================

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


# =============================================================================
# 2. GL BILLS
# =============================================================================
# Issues found:
#   - date / debit / credit / balance : str → parse/currency
#   - division : all empty, drop
#   - BALANCE FORWARD rows : drop
# =============================================================================

def clean_gl_bills(df):
    df = df.copy()
    df = df.drop(columns=["division"], errors="ignore")
    df = df[df["date"].str.upper().str.strip() != "BALANCE FORWARD"]
    df["date"]        = df["date"].pipe(clean_date)
    df["debit"]       = df["debit"].pipe(clean_currency)
    df["credit"]      = df["credit"].pipe(clean_currency)
    df["balance"]     = df["balance"].pipe(clean_currency)
    df["invoice"]     = df["invoice"].pipe(clean_text).str.upper()
    df["transaction"] = df["transaction"].pipe(clean_text)
    df["location"]    = df["location"].pipe(clean_text).str.upper()
    df["description"] = df["description"].pipe(clean_text).str.upper()
    df["party"]       = df["party"].pipe(clean_text)
    df["type"]        = df["type"].pipe(clean_text).str.title()
    return df.reset_index(drop=True)


# =============================================================================
# 3. BILLS
# =============================================================================
# Issues found:
#   - dates : str → parse
#   - amounts : str → currency
#   - created_by : all empty, drop
# =============================================================================

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


# =============================================================================
# 4. IN_TRANSIT
# =============================================================================
# Issues found:
#   - rail_eta : "Donotshow" → NaN, 100% null → drop
#   - initiated_on : "Initiated On" prefix → strip then parse
# =============================================================================

def clean_in_transit(df):
    df = df.copy()
    df = df.replace({"Donotshow": np.nan, "donotshow": np.nan})
    if "initiated_on" in df.columns:
        df["initiated_on"] = df["initiated_on"].astype(str)\
            .str.replace("Initiated On", "", case=False).str.strip()
    for col in ["port_eta", "rail_eta", "location_eta", "lfd", "eta_date", "initiated_on"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    for col in ["sipl", "supplier", "ship_to_location", "purchase_location",
                "vessel", "sipl_status", "status", "fr_forwarder", "departure_port"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    df = df.drop(columns=["rail_eta"], errors="ignore")
    return df.reset_index(drop=True)


# =============================================================================
# 5. OPEN_PO
# =============================================================================

def clean_open_po(df):
    df = df.copy()
    for col in ["po_date", "req_ship_date", "planned_ex_factory_date",
                "etd_port", "booked_eta_port", "eta_port", "location_eta"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_date)
    df["quantity"] = df["quantity"].pipe(clean_numeric)
    df["slabs"]    = df["slabs"].pipe(clean_numeric)
    df["unitcost"] = df["unitcost"].pipe(clean_currency)
    df["totalcost"]= df["totalcost"].pipe(clean_currency)
    for col in ["po", "sup_so", "origin", "ship_to_location", "purchase_location",
                "product", "type", "category", "supplier", "uom",
                "po_status", "vessel", "freight_forwarder"]:
        if col in df.columns:
            df[col] = df[col].pipe(clean_text)
    return df.reset_index(drop=True)


# =============================================================================
# 6. INVENTORY_INTRANSIT
# =============================================================================

def clean_inventory_intransit(df):
    df = df.copy()
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


# =============================================================================
# 7. BOOKINGS
# =============================================================================
# Issues found:
#   - notes : 100% null → drop
#   - etd/eta : mixed formats → parse
# =============================================================================

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


# =============================================================================
# 8. INVENTORY_RECEIVED
# =============================================================================
# Issues found:
#   - dates : str → parse
#   - fob/landed : str → currency
#   - quantity/slabs : str → numeric
# =============================================================================

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

# =============================================================================
# 9. SHIPMENT_MAPPING
# =============================================================================
# Purpose:
#   Build clean mapping between container ↔ PO ↔ SIPL
#
# Rules:
#   - Keep only valid ISO containers
#   - Extract container from messy strings
#   - Clean PO and SIPL as text
#   - Drop everything else
# =============================================================================

def clean_shipment_mapping(df):
    df = df.copy()

    # -------------------------------------------------------------------------
    # CLEAN KEYS
    # -------------------------------------------------------------------------
    df["po_number"] = df["p_o"].pipe(clean_text)
    df["sipl_number"] = df["sipl"].pipe(clean_text)

    # -------------------------------------------------------------------------
    # EXTRACT + CLEAN CONTAINER
    # -------------------------------------------------------------------------
    df["container_id"] = df["container"].apply(clean_container_strict)

    # -------------------------------------------------------------------------
    # FILTER — KEEP ONLY VALID CONTAINERS
    # -------------------------------------------------------------------------
    df = df[df["container_id"].notna()].copy()



    # -------------------------------------------------------------------------
    # DROP DUPLICATES (SAFETY)
    # -------------------------------------------------------------------------
    df = df.drop_duplicates().reset_index(drop=True)

    return df

# =============================================================================
# RUN ALL CLEANING FUNCTIONS
# =============================================================================

print("\n" + "=" * 60)
print("  CLEANING ALL DATAFRAMES")
print("=" * 60)

freight_bills        = clean_freight_bills(freight_bills)
gl_bills             = clean_gl_bills(gl_bills)
bills                = clean_bills(bills)
in_transit           = clean_in_transit(in_transit)
open_po              = clean_open_po(open_po)
inventory_intransit  = clean_inventory_intransit(inventory_intransit)
bookings             = clean_bookings(bookings)
inventory_received   = clean_inventory_received(inventory_received)
shipment_mapping     = clean_shipment_mapping(shipment_mapping)
print("\n✅ All dataframes cleaned successfully.")


# =============================================================================
# SQL SERVER CONNECTION
# =============================================================================

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost\\SQLEXPRESS01;"
    "DATABASE=logistics_db;"
    "Trusted_Connection=yes"
)
engine = create_engine(
    f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn_str)}",
    fast_executemany=True
)

with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("\n✅ Connected to logistics_db on localhost\\SQLEXPRESS01")


# =============================================================================
# LOAD ALL TABLES
# =============================================================================

def load_to_sql(df, table_name):
    print(f"\nLoading [{table_name}]...")
    print(f"  Rows: {len(df):,} | Cols: {df.shape[1]}")
    df.to_sql(table_name, engine, if_exists="replace", index=False, chunksize=500)
    print(f"  ✅ [{table_name}] loaded successfully")

print("\n" + "=" * 60)
print("  LOADING TO SQL SERVER")
print("=" * 60)

load_to_sql(freight_bills,       "freight_bills")
load_to_sql(gl_bills,            "gl_bills")
load_to_sql(bills,               "bills")
load_to_sql(bookings,            "bookings")
load_to_sql(inventory_received,  "inventory_received")
load_to_sql(in_transit,          "in_transit")
load_to_sql(open_po,             "open_po")
load_to_sql(inventory_intransit, "inventory_intransit")
load_to_sql(shipment_mapping, "shipment_mapping")

print("\n✅ All tables loaded into logistics_db successfully.")


# =============================================================================
# QC SNAPSHOT
# =============================================================================
# Runs after every ETL load.
# Flags current QC errors in team data and APPENDS to qc_snapshot.
# History is never deleted — this is the audit trail.
#
# CRITICAL errors (SOP Section 8):
#   NOT_IN_SPS          — team logged it, not in SPS
#   DUPLICATE_INVOICE   — same invoice more than once in team
#   MISSING_CONTAINER   — no container in team tracker
#   AMOUNT_NULL         — amount missing
#   CONTAINER_MISMATCH  — team container ≠ SPS container
#   AMOUNT_MISMATCH     — |team - SPS| > $1.00
#   INVALID_GL_ACCOUNT  — GL account not 1275 or 1313
#
# STANDARD errors (SOP Section 8):
#   INVOICE_DOUBLE_SPACE — extra spaces in invoice number
#   INVOICE_TAB_CHAR     — tab character in invoice number
#   INVOICE_TYPO_CHAR    — unexpected character (e.g. ?)
#   DATE_MISMATCH        — invoice date in team ≠ SPS
# =============================================================================

print("\n" + "=" * 60)
print("  QC SNAPSHOT — GENERATING AUDIT TRAIL")
print("=" * 60)

CM_RE_QC      = re.compile(r"\bCM$", re.IGNORECASE)
JUNK_RE_QC    = re.compile(r"^\d{1,4}$")
run_date      = pd.Timestamp.today().normalize()

# Normalize invoice key: uppercase + collapse whitespace
def norm_key(val):
    if pd.isna(val): return ""
    return re.sub(r"[\s\xa0]+", " ", str(val).upper().strip())

# Prepare team — drop PENDING entirely before QC
team_qc = freight_bills.copy()
team_qc["invoice_key"] = team_qc["sps_inv_no"].apply(norm_key)
team_qc = team_qc[
    ~team_qc["invoice_key"].str.contains(r"^PEND", case=False, na=False)
].copy()

# Prepare bills lookup: 2026 freight only, drop PENDING
bills_qc = bills.copy()
bills_qc = bills_qc[bills_qc["non_inventory_vendor"].notna()]
bills_qc["invoice_dt"] = pd.to_datetime(bills_qc["invoice_dt"], errors="coerce")
BILLS_START_DATE = pd.Timestamp("2025-12-20")
bills_qc = bills_qc[bills_qc["invoice_dt"] >= BILLS_START_DATE]
bills_qc["invoice_key"] = bills_qc["bill_inv"].apply(norm_key)
bills_qc = bills_qc[
    ~bills_qc["invoice_key"].str.contains(r"^PEND", case=False, na=False)
].copy()

# Merge team against bills
team_qc = team_qc.merge(
    bills_qc[["invoice_key", "amount", "container", "invoice_dt",
              "non_inventory_vendor"]].rename(columns={
        "amount"               : "sps_amount",
        "container"            : "sps_container",
        "invoice_dt"           : "sps_invoice_dt",
        "non_inventory_vendor" : "sps_vendor",
    }),
    on="invoice_key", how="left"
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

    # PENDING already dropped from team_qc before this loop
    # Skip CM and junk — no QC checks for these
    is_cm   = bool(CM_RE_QC.search(str(inv)))
    is_junk = bool(JUNK_RE_QC.match(str(inv))) or inv == ""

    if is_cm or is_junk:
        continue

    # --- STANDARD: invoice format issues ---
    raw_inv = str(row.get("sps_inv_no", ""))
    if re.search(r"  ", raw_inv):
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_DOUBLE_SPACE",
                       "error_detail": f"Double space in invoice number: '{raw_inv}'"})
    if "\t" in raw_inv:
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_TAB_CHAR",
                       "error_detail": f"Tab character in invoice number: '{raw_inv}'"})
    if re.search(r"\?", raw_inv):
        errors.append({**base, "error_type": "STANDARD", "error_code": "INVOICE_TYPO_CHAR",
                       "error_detail": f"Unexpected character in invoice number: '{raw_inv}'"})

    # --- CRITICAL: not in SPS ---
    if pd.isna(row.get("sps_amount")):
        errors.append({**base, "error_type": "CRITICAL", "error_code": "NOT_IN_SPS",
                       "error_detail": "Invoice not found in SPS bills (2026)"})
        continue  # No SPS data to compare against

    # --- CRITICAL: duplicate invoice ---
    dup_count = (team_qc["invoice_key"] == inv).sum()
    if dup_count > 1:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "DUPLICATE_INVOICE",
                       "error_detail": f"Invoice appears {dup_count} times in team tracker"})

    # --- CRITICAL: missing container ---
    if pd.isna(row.get("container")) or str(row.get("container", "")).strip() == "":
        errors.append({**base, "error_type": "CRITICAL", "error_code": "MISSING_CONTAINER",
                       "error_detail": "Container not recorded in team tracker"})

    # --- CRITICAL: amount null ---
    if pd.isna(row.get("amount")):
        errors.append({**base, "error_type": "CRITICAL", "error_code": "AMOUNT_NULL",
                       "error_detail": "Amount is missing in team tracker"})

    # --- CRITICAL: container mismatch ---
    team_cont = str(row.get("container", "")).strip().upper()
    sps_cont  = str(row.get("sps_container", "")).strip().upper()
    if team_cont and sps_cont and team_cont not in ("NAN", "") and \
       sps_cont not in ("NAN", "") and team_cont != sps_cont:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "CONTAINER_MISMATCH",
                       "error_detail": f"Team: '{team_cont}' | SPS: '{sps_cont}'"})

    # --- CRITICAL: amount mismatch (tolerance $1.00) ---
    team_amt = row.get("amount")
    sps_amt  = row.get("sps_amount")
    if pd.notna(team_amt) and pd.notna(sps_amt):
        diff = abs(float(team_amt) - float(sps_amt))
        if diff > 1.00:
            errors.append({**base, "error_type": "CRITICAL", "error_code": "AMOUNT_MISMATCH",
                           "error_detail": f"Team: ${float(team_amt):,.2f} | SPS: ${float(sps_amt):,.2f} | Diff: ${diff:,.2f}"})

    # --- CRITICAL: invalid GL account ---
    team_gl = row.get("gl_posted")
    if pd.notna(team_gl) and int(team_gl) not in {1275, 1313}:
        errors.append({**base, "error_type": "CRITICAL", "error_code": "INVALID_GL_ACCOUNT",
                       "error_detail": f"GL account '{int(team_gl)}' not in approved list (1275, 1313)"})

    # --- STANDARD: invoice date mismatch ---
    team_dt = row.get("invoice_date")
    sps_dt  = row.get("sps_invoice_dt")
    if pd.notna(team_dt) and pd.notna(sps_dt):
        if pd.Timestamp(team_dt).normalize() != pd.Timestamp(sps_dt).normalize():
            errors.append({**base, "error_type": "STANDARD", "error_code": "DATE_MISMATCH",
                           "error_detail": f"Team date: {str(team_dt)[:10]} | SPS date: {str(sps_dt)[:10]}"})

# Build and save snapshot
if errors:
    qc_snap = pd.DataFrame(errors)
    qc_snap["run_date"] = pd.to_datetime(qc_snap["run_date"])

    print(f"\n  QC errors found      : {len(qc_snap):,}")
    print(f"  Critical             : {(qc_snap['error_type'] == 'CRITICAL').sum():,}")
    print(f"  Standard             : {(qc_snap['error_type'] == 'STANDARD').sum():,}")
    print("\n  Error breakdown:")
    print(qc_snap["error_code"].value_counts().to_string())

    # APPEND only — never replace — this is the audit trail
    qc_snap.to_sql("qc_snapshot", engine, if_exists="append", index=False, chunksize=500)
    print(f"\n  ✅ QC snapshot appended → [qc_snapshot] | run_date: {run_date.date()} | rows: {len(qc_snap):,}")
else:
    print("\n  ✅ No QC errors found in this run")

print("\n" + "=" * 60)
print("  ✅ ETL COMPLETE")
print("=" * 60)