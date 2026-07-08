# -*- coding: utf-8 -*-
"""
build_dashboard_data.py (REDESIGNED) — Invoice Compliance Engine
Architectural Surfaces

OBJECTIVE:
  Rebuild invoice compliance to answer: "For every live container currently in
  transit, which invoices are Missing or Pending in SPS today?"
  Focus: CURRENT operational state, not historical lifecycle.

DATA SCOPE (5 SOURCES ONLY for Invoice Compliance):
  Operational:
    - In-Transit List by SIPL (current execution status)
    - Inventory In Transit (SKU details per SIPL)
  Financial:
    - Bills.xls (invoice registry)
    - Account Register 1313 (GL - Prepaid Container Freight)
    - Account Register 1275 (GL - Capitalized Inventory Freight)

DESIGN DECISIONS:
  1. Shipment universe: In-Transit + Inventory ONLY (no Bookings/Open PO)
  2. Business keys: Container + SIPL (primary); PO is supplemental
  3. Missing = no invoice + no Pending placeholder
  4. Pending = bill_inv contains "PEND"
  5. Every unmatched invoice audited & reported
  6. Compliance scored PER-SIPL

CATEGORIES:
  Required (4): Ocean Freight (OF), Customs, Duty, Drayage
  Tracked separately: Accessorial (never gates status)

RUN:
    python build_dashboard_data_redesigned.py
"""

import re
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_currency, clean_date,
    clean_numeric, clean_text, clean_container, is_pending,
)

# =============================================================================
# CONFIG
# =============================================================================

SOURCE_DIR = Path(__file__).parent
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = pd.Timestamp.today().normalize()

REQUIRED_CATEGORIES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]
ACCESSORIAL_LABEL = "ACCESSORIAL"

FILES = {
    # INVOICE COMPLIANCE SOURCES (5 only)
    "in_transit":         SOURCE_DIR / "In-Transit List by SIPL.xls",
    "inventory_intransit": SOURCE_DIR / "Inventory In Transit - Detail .xls",
    "bills":              SOURCE_DIR / "Bills.xls",
    "account_1275":       SOURCE_DIR / "Account Register_ 1275 - Capitalized Inventory Freight.xls",
    "account_1313":       SOURCE_DIR / "Account Register_ 1313 - Prepaid Container Freight.xls",

    # OPERATIONAL DATA (for other dashboards: Booking, Container Movement)
    "open_po":            SOURCE_DIR / "Open PO List.xls",
    "bookings":           Path(r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Bookings Tracker.xlsx"),
    "supplier_invoices":  SOURCE_DIR / "Supplier Invoices.xls",
}

print("=" * 80)
print("  LOGISTICS CONTROL TOWER — DASHBOARD DATA BUILD")
print(f"  Run date: {TODAY.date()}")
print("  Invoice Compliance: Using 5 data sources (In-Transit, Inventory, Bills, GL 1275, GL 1313)")
print("=" * 80)

# =============================================================================
# LOAD + CLEAN ALL SOURCES
# =============================================================================
print("\n--- LOADING + CLEANING SOURCES ---")

in_transit = standardize_columns(load_html_table(FILES["in_transit"]))
in_transit = in_transit.replace({"Donotshow": np.nan, "donotshow": np.nan})
in_transit["container"] = in_transit["container"].apply(clean_container)
if "initiated_on" in in_transit.columns:
    in_transit["initiated_on"] = (
        in_transit["initiated_on"].astype(str)
        .str.replace("Initiated On", "", case=False)
        .str.strip()
    )
for col in ["port_eta", "location_eta", "lfd", "eta_date", "initiated_on"]:
    if col in in_transit.columns:
        in_transit[col] = clean_date(in_transit[col])
for col in ["sipl", "supplier", "ship_to_location", "purchase_location",
            "vessel", "sipl_status", "status", "fr_forwarder", "departure_port"]:
    if col in in_transit.columns:
        in_transit[col] = clean_text(in_transit[col])
in_transit = in_transit.drop(columns=["rail_eta"], errors="ignore")
print(f"  in_transit           : {in_transit.shape}")

inventory_intransit = standardize_columns(load_html_table(FILES["inventory_intransit"]))
inventory_intransit["container"] = inventory_intransit["container"].apply(clean_container)
for col in ["sipl_date", "req_ship_date", "ship_b_l_date", "eta_date"]:
    if col in inventory_intransit.columns:
        inventory_intransit[col] = clean_date(inventory_intransit[col])
for col in ["quantity", "slabs", "unit_cost", "total_cost"]:
    if col in inventory_intransit.columns:
        inventory_intransit[col] = clean_numeric(inventory_intransit[col])
for col in ["name", "sku", "type", "category", "subcategory", "group",
            "supplier", "sipl", "freight_forwarder", "departure_port",
            "arrival_port", "units", "bill_to", "ship_to"]:
    if col in inventory_intransit.columns:
        inventory_intransit[col] = clean_text(inventory_intransit[col])
print(f"  inventory_intransit  : {inventory_intransit.shape}")

# Load GL first to get date range
def clean_gl(df, gl_account):
    df = df.copy()
    df = df.drop(columns=["division", "reconciled"], errors="ignore")
    df["date"] = clean_date(df["date"])
    df["debit"] = clean_currency(df["debit"])
    df["credit"] = clean_currency(df["credit"])
    df["balance"] = clean_currency(df["balance"])
    df["invoice"] = clean_text(df["invoice"]).str.upper()
    df["description"] = clean_text(df["description"]).str.upper()
    df["party"] = clean_text(df["party"])
    df["type"] = clean_text(df["type"])
    df["gl_account"] = gl_account
    return df

account_1275 = clean_gl(standardize_columns(load_html_table(FILES["account_1275"])), 1275)
account_1313 = clean_gl(standardize_columns(load_html_table(FILES["account_1313"])), 1313)
gl_bills = pd.concat([account_1275, account_1313], ignore_index=True)
gl_min_date = gl_bills["date"].min()
print(f"  gl_bills (1275+1313) : {gl_bills.shape}  (date range: {gl_min_date.date()} to {gl_bills['date'].max().date()})")

# Load Bills
bills = standardize_columns(load_html_table(FILES["bills"]))
bills = bills.rename(columns={"bill_inv": "bill_inv", "sipl_inv": "sipl", "container": "container"})
bills["container"] = bills["container"].apply(clean_container)
bills["sipl"] = clean_text(bills.get("sipl"))
bills["bill_inv"] = clean_text(bills.get("bill_inv")).str.upper()
bills["vendor"] = clean_text(bills.get("non_inventory_vendor"))
for col in ["invoice_dt", "due_date", "sipl_inv_dt", "sipl_ship_dt"]:
    if col in bills.columns:
        bills[col] = clean_date(bills[col])
for col in ["amount", "balance_due", "sipl_amount"]:
    if col in bills.columns:
        bills[col] = clean_currency(bills[col])
bills["is_pending"] = is_pending(bills["bill_inv"])

# DATA QUALITY FIX: Filter Bills (GL range + remove duplicates + require container)
bills_before_filter = len(bills)
duplicates_before = bills_before_filter - bills["bill_inv"].nunique()
no_container_before = (bills["container"].isna() | (bills["container"] == "NAN")).sum()

# Step 1: Filter to GL date range
bills = bills[bills["invoice_dt"] >= gl_min_date].copy()
print(f"  bills (RAW)          : {bills_before_filter:,} rows  (duplicates: {duplicates_before:,}, no container: {no_container_before:,})")
print(f"  bills (GL-aligned)   : {len(bills):,} rows  (filtered to {gl_min_date.date()} onwards)")
print(f"    - Removed historical: {bills_before_filter - len(bills):,} rows outside GL range")

# Step 2: Remove bills without container (NOT useful for invoice compliance)
bills_before_container = len(bills)
bills = bills[bills["container"].notna() & (bills["container"] != "NAN")].copy()
print(f"    - Removed no-container: {bills_before_container - len(bills):,} bills (not useful for invoice compliance)")

# Step 3: Remove duplicate bills (keep latest by date)
bills_before_dedup = len(bills)
bills = bills.sort_values("invoice_dt").drop_duplicates(subset=["bill_inv"], keep="last").reset_index(drop=True)
print(f"    - Removed duplicates: {bills_before_dedup - len(bills):,} bills")
print(f"    - Final bills: {bills.shape[0]:,} rows  (all have containers, GL-aligned, pending: {bills['is_pending'].sum():,})")

def clean_gl(df, gl_account):
    df = df.copy()
    df = df.drop(columns=["division", "reconciled"], errors="ignore")
    df["date"] = clean_date(df["date"])
    df["debit"] = clean_currency(df["debit"])
    df["credit"] = clean_currency(df["credit"])
    df["balance"] = clean_currency(df["balance"])
    df["invoice"] = clean_text(df["invoice"]).str.upper()
    df["description"] = clean_text(df["description"]).str.upper()
    df["party"] = clean_text(df["party"])
    df["type"] = clean_text(df["type"])
    df["gl_account"] = gl_account
    return df

account_1275 = clean_gl(standardize_columns(load_html_table(FILES["account_1275"])), 1275)
account_1313 = clean_gl(standardize_columns(load_html_table(FILES["account_1313"])), 1313)
gl_bills = pd.concat([account_1275, account_1313], ignore_index=True)
print(f"  gl_bills (1275+1313) : {gl_bills.shape}")

# Only "Bill" type rows are logistics invoices (exclude "SupplierInvoice" merchandise costs)
gl_freight = gl_bills[gl_bills["type"] == "Bill"].copy()
print(f"    -> gl 'Bill' rows (freight) : {len(gl_freight):,}")

# Load operational data for other dashboards
print("\n  Loading operational data (for Booking/Container Movement dashboards)...")

open_po = standardize_columns(load_html_table(FILES["open_po"]))
open_po["container"] = open_po["container"].apply(clean_container)
for col in ["po_date", "req_ship_date", "planned_ex_factory_date", "etd_port",
            "booked_eta_port", "eta_port", "location_eta"]:
    if col in open_po.columns:
        open_po[col] = clean_date(open_po[col])
for col in ["quantity", "slabs", "unitcost", "totalcost"]:
    if col in open_po.columns:
        open_po[col] = clean_numeric(open_po[col])
for col in ["po", "sup_so", "origin", "ship_to_location", "purchase_location",
            "product", "type", "category", "supplier", "uom", "po_status",
            "vessel", "freight_forwarder"]:
    if col in open_po.columns:
        open_po[col] = clean_text(open_po[col])
print(f"  open_po              : {open_po.shape}")

bookings_path = FILES["bookings"]
if bookings_path and bookings_path.exists():
    bookings = standardize_columns(pd.read_excel(bookings_path, sheet_name="Event_Log"))
    bookings = bookings.drop(columns=["notes"], errors="ignore")
    bookings["event_date"] = clean_date(bookings["event_date"])
    bookings["etd"] = clean_date(bookings["etd"])
    bookings["eta"] = clean_date(bookings["eta"])
    bookings["event_status"] = clean_text(bookings["event_status"]).str.upper()
    bookings["container_id"] = bookings["container_id"].apply(clean_container)
    for col in ["vessel", "reason", "updated_by"]:
        if col in bookings.columns:
            bookings[col] = clean_text(bookings[col])
    print(f"  bookings             : {bookings.shape}")
else:
    bookings = pd.DataFrame(columns=["event_date", "etd", "eta", "event_status", "container_id", "vessel", "reason", "updated_by", "po_number"])
    print("  bookings             : 0 rows (file not found)")

supplier_invoices = standardize_columns(load_html_table(FILES["supplier_invoices"]))
supplier_invoices["container"] = supplier_invoices["container"].apply(clean_container)
supplier_invoices["sipl"] = clean_text(supplier_invoices.get("sipl"))
supplier_invoices["p_o"] = clean_text(supplier_invoices.get("p_o"))
print(f"  supplier_invoices    : {supplier_invoices.shape}")

print("\n[OK] All sources loaded")

# =============================================================================
# STEP 1: CREATE ACTIVE SHIPMENT UNIVERSE (In-Transit + Inventory only)
# =============================================================================
print("\n--- STEP 1: CREATE ACTIVE SHIPMENT UNIVERSE ---")

sipl_master = in_transit.copy()
sipl_master = sipl_master.rename(columns={"fr_forwarder": "freight_forwarder"})

print(f"  sipl_master (from In-Transit) : {sipl_master.shape}")
print(f"  Active containers: {sipl_master['container'].nunique():,}")
print(f"  Active SIPLs: {sipl_master['sipl'].nunique():,}")

# =============================================================================
# STEP 2: BUILD SHIPMENT MAPPING (For Booking & Container dashboards)
# CRITICAL: Keep po_number for backward compatibility with other dashboards
# =============================================================================
print("\n--- STEP 2: BUILD SHIPMENT MAPPING (Backward Compatible) ---")

# Reconstruct shipment_mapping from multiple sources (same as old code)
# This ensures Booking/Container dashboards continue to work
pairs = []

sm1 = supplier_invoices[["container", "sipl", "p_o"]].rename(columns={"p_o": "po_number"})
pairs.append(sm1)

sm2 = open_po[["container", "po"]].rename(columns={"po": "po_number"})
sm2["sipl"] = np.nan
pairs.append(sm2[["container", "sipl", "po_number"]])

sm3 = bookings[["container_id", "po_number"]].rename(columns={"container_id": "container"})
sm3["sipl"] = np.nan
pairs.append(sm3[["container", "sipl", "po_number"]])

sm4 = bills[["container", "sipl"]].copy()
sm4["po_number"] = np.nan
pairs.append(sm4[["container", "sipl", "po_number"]])

shipment_mapping_data = pd.concat(pairs, ignore_index=True)
shipment_mapping_data["po_number"] = shipment_mapping_data["po_number"].astype(str).replace({"nan": np.nan, "<NA>": np.nan})
shipment_mapping_data = shipment_mapping_data[
    shipment_mapping_data["container"].notna() | shipment_mapping_data["sipl"].notna()
].drop_duplicates().reset_index(drop=True)

# Rename to match old schema (for backward compatibility)
shipment_mapping_data = shipment_mapping_data.rename(columns={
    "container": "container_id",
    "sipl": "sipl_number"
})

print(f"  shipment_mapping (Backward Compatible): {shipment_mapping_data.shape}")
print(f"    - Distinct containers: {shipment_mapping_data['container_id'].nunique():,}")
print(f"    - Distinct SIPLs: {shipment_mapping_data['sipl_number'].nunique():,}")
print(f"    - Rows with po_number: {shipment_mapping_data['po_number'].notna().sum():,}")

# =============================================================================
# STEP 3: ENRICH SHIPMENT MASTER (Container + SIPL as primary keys)
# =============================================================================
print("\n--- STEP 3: ENRICH SHIPMENT MASTER ---")

def eta_bucket(days):
    if pd.isna(days):
        return "Unknown ETA"
    if days < 0:
        return "Past ETA"
    if days == 0:
        return "Arriving Today"
    if days <= 3:
        return "Next 3 Days"
    if days <= 7:
        return "Next 7 Days"
    return "Future"

sipl_master["days_to_port_eta"] = (sipl_master["port_eta"] - TODAY).dt.days
sipl_master["operational_priority"] = sipl_master["days_to_port_eta"].apply(eta_bucket)

# Add PO numbers from shipment_mapping for invoice compliance display
po_by_sipl = (
    shipment_mapping_data.dropna(subset=["sipl_number", "po_number"])
    .groupby("sipl_number")["po_number"]
    .apply(lambda s: ", ".join(sorted(set(str(x) for x in s if x))))
    .rename("po_numbers")
    .rename_axis("sipl")
)
sipl_master = sipl_master.merge(po_by_sipl, on="sipl", how="left")
sipl_master["po_numbers"] = sipl_master["po_numbers"].fillna("")

print(f"  Operational priority breakdown:")
print(sipl_master["operational_priority"].value_counts().to_string())

# =============================================================================
# STEP 4: MATCH BILLS TO SHIPMENTS & AUDIT
# =============================================================================
print("\n--- STEP 4: MATCH BILLS TO SHIPMENTS ---")

# Build mappings: SIPL -> Container(s), Container -> SIPL(s)
# Using active shipment data (sipl_master) for matching
sipl_to_containers = (
    sipl_master[sipl_master["container"].notna()]
    .groupby("sipl")["container"]
    .apply(set)
)

container_to_sipls = (
    sipl_master[sipl_master["container"].notna()]
    .groupby("container")["sipl"]
    .apply(set)
)

matched_bills = []
unmatched_bills = []
match_summary = {"matched_by_container_sipl": 0, "matched_by_sipl": 0, "matched_by_container": 0, "unmatched": 0}

for idx, bill in bills.iterrows():
    container = bill.get("container")
    sipl = bill.get("sipl")
    matched = False

    # Priority 1: Match by SIPL (most authoritative key in In-Transit)
    if pd.notna(sipl) and sipl in sipl_to_containers:
        matched_bills.append({**bill.to_dict(), "match_type": "SIPL", "matched_sipl": sipl})
        match_summary["matched_by_sipl"] += 1
        matched = True

    # Priority 2: Match by Container (if SIPL didn't match)
    elif pd.notna(container) and container in container_to_sipls:
        # Get ONE of the SIPLs for this container (pick first alphabetically for consistency)
        matched_sipl = sorted(container_to_sipls[container])[0]
        matched_bills.append({**bill.to_dict(), "match_type": "CONTAINER", "matched_sipl": matched_sipl})
        match_summary["matched_by_container"] += 1
        matched = True

    # Unmatched
    if not matched:
        reason = "NO_MATCH_FOUND"
        if pd.isna(container) and pd.isna(sipl):
            reason = "MISSING_BOTH_CONTAINER_AND_SIPL"
        elif pd.isna(container):
            reason = f"CONTAINER_NULL (SIPL={sipl})"
        elif pd.isna(sipl):
            reason = f"SIPL_NULL (CONTAINER={container})"
        unmatched_bills.append({**bill.to_dict(), "reason": reason})
        match_summary["unmatched"] += 1

matched_bills_df = pd.DataFrame(matched_bills)
unmatched_bills_df = pd.DataFrame(unmatched_bills) if unmatched_bills else pd.DataFrame(columns=bills.columns.tolist() + ["reason"])

print(f"  Bills matched:")
for match_type, count in match_summary.items():
    print(f"    {match_type}: {count:,}")
total_matched = match_summary['matched_by_sipl'] + match_summary['matched_by_container']
print(f"\n  Total matched: {total_matched:,} of {len(bills):,}")
print(f"  Match rate: {total_matched / len(bills):.1%}")

if len(unmatched_bills_df) > 0:
    print(f"\n  Top unmatched reasons:")
    print(unmatched_bills_df["reason"].value_counts().head(10).to_string())

# =============================================================================
# STEP 5: CLASSIFY INVOICES BY GL DESCRIPTION
# =============================================================================
print("\n--- STEP 5: CLASSIFY INVOICES BY GL DESCRIPTION ---")

try:
    from rapidfuzz import process, fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:
    HAVE_RAPIDFUZZ = False
    print("  [WARN] rapidfuzz not installed")

def normalize_category(text):
    if pd.isna(text):
        return None
    t = str(text).strip().upper()
    if t == "" or t in {"MISC", "PO", "AMS", "ISF", "ISC"} or re.match(r"^PO\s*#", t):
        pass
    if t == "OF" or "OCEAN FREIGHT" in t or "AIR FREIGHT" in t or "AIRFREIGHT" in t:
        return "OF"
    if "CUSTOM" in t or "BROKERAGE" in t:
        return "CUSTOMS"
    if "DUTY" in t:
        return "DUTY"
    if "DRAY" in t:
        return "DRAYAGE"
    if re.match(r"^PO\s*#?\s*\d", t):
        return None
    accessorial_terms = ["DETENTION", "DEMURRAGE", "PER DIEM", "CHASSIS", "EXAM",
                          "TERMINAL", "ADMIN FEE", "MISC", "CREDIT MEMO", "AMS",
                          "ISF", "ISC", "FLATBED", "PER PULL", "DAMAGE"]
    if any(term in t for term in accessorial_terms):
        return ACCESSORIAL_LABEL
    if HAVE_RAPIDFUZZ:
        match = process.extractOne(t, REQUIRED_CATEGORIES, scorer=fuzz.ratio)
        if match and match[1] >= 85:
            return match[0]
    return None

gl_freight["category"] = gl_freight["description"].apply(normalize_category)
gl_lookup = (
    gl_freight[gl_freight["category"].notna()]
    [["invoice", "category", "gl_account", "date", "party"]]
    .drop_duplicates(subset=["invoice", "category"])
)

print(f"  GL invoices classified: {gl_freight['category'].notna().sum():,} of {len(gl_freight):,}")
print(f"  Breakdown by category:")
print(gl_freight[gl_freight["category"].notna()]["category"].value_counts().to_string())

# =============================================================================
# STEP 6: BUILD INVOICE EVENTS (Confirmed + Inferred Pending)
# =============================================================================
print("\n--- STEP 6: BUILD INVOICE EVENTS ---")

# Confirmed (non-pending) bills matched to GL
confirmed_bills = matched_bills_df[~matched_bills_df["is_pending"]].copy()
gl_matched = confirmed_bills.merge(
    gl_lookup, left_on="bill_inv", right_on="invoice", how="inner"
)
gl_matched = gl_matched.rename(columns={"bill_inv": "invoice_number", "invoice_dt": "invoice_date"})
gl_matched["sipl"] = gl_matched["matched_sipl"]  # Use the matched SIPL from our matching logic
gl_matched["source_tier"] = "GL_CONFIRMED"
gl_matched["is_pending"] = False

print(f"  GL-confirmed events: {len(gl_matched):,} of {len(confirmed_bills):,} confirmed bills")

# Pending bills (may have inferred category)
pending_bills = matched_bills_df[matched_bills_df["is_pending"]].copy()
pending_bills["invoice_number"] = pending_bills["bill_inv"]
pending_bills["sipl"] = pending_bills["matched_sipl"]  # Use the matched SIPL from our matching logic
pending_bills["category"] = None
pending_bills["source_tier"] = "PENDING_UNATTRIBUTED"
pending_bills["is_pending"] = True

print(f"  Pending events (unattributed): {len(pending_bills):,}")

# Combine
event_cols = ["container", "sipl", "category", "is_pending", "amount", "source_tier", "invoice_number"]
invoice_events = pd.concat([
    gl_matched[event_cols],
    pending_bills[event_cols],
], ignore_index=True)
invoice_events = invoice_events[invoice_events["category"].notna() | invoice_events["is_pending"]].reset_index(drop=True)

print(f"  Total invoice events: {len(invoice_events):,}")

# =============================================================================
# STEP 7: BUILD INVOICE COMPLIANCE PER SIPL
# =============================================================================
print("\n--- STEP 7: BUILD INVOICE COMPLIANCE (using v3 corrected logic) ---")

# Run the v3 ETL to generate correct invoice_compliance data
import subprocess
print("  Running build_dashboard_data_v3.py...")
try:
    result = subprocess.run(
        [sys.executable, "build_dashboard_data_v3.py"],
        capture_output=True,
        text=True,
        check=True
    )
    print("  [OK] v3 logic completed")
    # Load the generated CSV
    invoice_compliance = pd.read_csv('invoice_compliance_v3_final.csv')
    print(f"  [OK] Loaded invoice_compliance: {len(invoice_compliance)} SIPLs")
    print(f"       Complete: {(invoice_compliance['overall_status'] == 'Complete').sum()}")
    print(f"       Pending: {(invoice_compliance['overall_status'] == 'Pending').sum()}")
    print(f"       Missing: {(invoice_compliance['overall_status'] == 'Missing').sum()}")
except subprocess.CalledProcessError as e:
    print(f"  [ERROR] v3 logic failed: {e.stderr}")
    invoice_compliance = None
except FileNotFoundError:
    print("  [WARNING] v3 CSV not found, using legacy logic")
    invoice_compliance = None

if invoice_compliance is None:
    print("\n--- STEP 7 (LEGACY FALLBACK): BUILD INVOICE COMPLIANCE (PER-SIPL) ---")

required_events = invoice_events[invoice_events["category"].isin(REQUIRED_CATEGORIES)].copy()

# Confirmed invoices per (SIPL, category)
confirmed_required = required_events[~required_events["is_pending"]]
confirmed_by_sipl = (
    confirmed_required.dropna(subset=["sipl", "category"])
    .drop_duplicates(subset=["sipl", "category"])
    .assign(flag=1)
    .pivot_table(index="sipl", columns="category", values="flag", fill_value=0)
    .reindex(columns=REQUIRED_CATEGORIES, fill_value=0)
)

# Pending invoices per (SIPL, category)
pending_required = required_events[required_events["is_pending"]]
pending_by_sipl = (
    pending_required.dropna(subset=["sipl", "category"])
    .drop_duplicates(subset=["sipl", "category"])
    .assign(flag=1)
    .pivot_table(index="sipl", columns="category", values="flag", fill_value=0)
    .reindex(columns=REQUIRED_CATEGORIES, fill_value=0)
)

# Build per-SIPL compliance
invoice_compliance_rows = []
for _, sipl_row in sipl_master.iterrows():
    sipl_id = sipl_row["sipl"]
    container_id = sipl_row["container"]

    # Define arrival_status based on days_to_port_eta
    days_eta = sipl_row.get("days_to_port_eta")
    if pd.isna(days_eta):
        arrival_status = "Unknown"
    elif days_eta < 0:
        arrival_status = "Past ETA"
    elif days_eta == 0:
        arrival_status = "Today"
    else:
        arrival_status = "Approaching"

    row = {
        "sipl": sipl_id,
        "container": container_id,
        "po_numbers": sipl_row.get("po_numbers", ""),
        "supplier": sipl_row.get("supplier"),
        "freight_forwarder": sipl_row.get("freight_forwarder"),
        "destination": sipl_row.get("ship_to_location"),
        "port_eta": sipl_row.get("port_eta"),
        "days_to_port_eta": sipl_row.get("days_to_port_eta"),
        "operational_priority": sipl_row.get("operational_priority"),
        "arrival_status": arrival_status,
    }

    comp_row = confirmed_by_sipl.loc[sipl_id] if sipl_id in confirmed_by_sipl.index else pd.Series(0, index=REQUIRED_CATEGORIES)
    pend_row = pending_by_sipl.loc[sipl_id] if sipl_id in pending_by_sipl.index else pd.Series(0, index=REQUIRED_CATEGORIES)

    missing_cats, pending_cats = [], []
    for cat in REQUIRED_CATEGORIES:
        if comp_row[cat] == 1:
            row[f"{cat}_status"] = "Complete"
        elif pend_row[cat] == 1:
            row[f"{cat}_status"] = "Pending"
            pending_cats.append(cat)
        else:
            row[f"{cat}_status"] = "Missing"
            missing_cats.append(cat)

    row["missing_categories"] = ", ".join(missing_cats) if missing_cats else ""
    row["pending_categories"] = ", ".join(pending_cats) if pending_cats else ""

    if not missing_cats and not pending_cats:
        row["overall_status"] = "Complete"
    elif missing_cats and not pending_cats:
        row["overall_status"] = "Missing"
    else:
        row["overall_status"] = "Missing" if missing_cats else "Pending"

    invoice_compliance_rows.append(row)

invoice_compliance = pd.DataFrame(invoice_compliance_rows)
print(f"  invoice_compliance: {invoice_compliance.shape}")
print(invoice_compliance["overall_status"].value_counts().to_string())

# =============================================================================
# STEP 8: BUILD RECONCILIATION AUDIT
# =============================================================================
print("\n--- STEP 8: BUILD RECONCILIATION AUDIT ---")

audit_rows = []
audit_rows.append({
    "metric": "Total Bills",
    "count": len(bills),
})
audit_rows.append({
    "metric": "Bills Matched to Shipments",
    "count": len(matched_bills_df),
})
audit_rows.append({
    "metric": "Bills Unmatched",
    "count": len(unmatched_bills_df),
})
audit_rows.append({
    "metric": "Matched Bills -> GL Confirmed",
    "count": len(gl_matched),
})
audit_rows.append({
    "metric": "Matched Bills -> Pending (Unattributed)",
    "count": len(pending_bills),
})

reconciliation_audit = pd.DataFrame(audit_rows)
print("\nReconciliation Summary:")
print(reconciliation_audit.to_string(index=False))

# =============================================================================
# STEP 8: VALIDATION & CLEANUP
# =============================================================================
print("\n--- STEP 8: VALIDATION & CLEANUP ---")

# Ensure all dataframes have clean column names (no trailing/leading spaces)
def clean_dataframe_columns(df):
    """Clean column names: strip whitespace, lowercase"""
    df = df.copy()
    df.columns = df.columns.str.strip()
    return df

invoice_compliance = clean_dataframe_columns(invoice_compliance)
unmatched_bills_df = clean_dataframe_columns(unmatched_bills_df)
reconciliation_audit = clean_dataframe_columns(reconciliation_audit)
invoice_events = clean_dataframe_columns(invoice_events)
bills = clean_dataframe_columns(bills)
in_transit = clean_dataframe_columns(in_transit)
inventory_intransit = clean_dataframe_columns(inventory_intransit)
open_po = clean_dataframe_columns(open_po)
bookings = clean_dataframe_columns(bookings)
supplier_invoices = clean_dataframe_columns(supplier_invoices)
gl_bills = clean_dataframe_columns(gl_bills)

# Validate invoice_compliance has required columns
required_cols = ["sipl", "container", "overall_status", "missing_categories", "pending_categories"]
missing_cols = [c for c in required_cols if c not in invoice_compliance.columns]
if missing_cols:
    print(f"  [ERROR] invoice_compliance missing columns: {missing_cols}")
    print(f"  Available columns: {invoice_compliance.columns.tolist()}")
    raise ValueError(f"Missing required columns: {missing_cols}")

print(f"  [OK] invoice_compliance validated ({len(invoice_compliance)} SIPLs)")
print(f"  [OK] unmatched_bills validated ({len(unmatched_bills_df)} unmatched)")

# =============================================================================
# STEP 9: WRITE OUTPUT
# =============================================================================
print("\n--- STEP 9: WRITE OUTPUT ---")

today_str = TODAY.strftime("%Y-%m-%d")
dated_file = OUTPUT_DIR / f"dashboard_data_{today_str}.xlsx"
latest_file = OUTPUT_DIR / "dashboard_data.xlsx"

# Prepare sheets
# CRITICAL: shipment_mapping must have po_number for Booking/Container dashboards
SHEETS = {
    "in_transit":           in_transit,
    "inventory_intransit":  inventory_intransit,
    "shipment_mapping":     shipment_mapping_data,  # Includes po_number (backward compatible)
    "bills":                bills,
    "invoice_events":       invoice_events,
    "invoice_compliance":   invoice_compliance,
    "unmatched_bills":      unmatched_bills_df,
    "reconciliation_audit": reconciliation_audit,

    # Operational data for other dashboards (UNCHANGED)
    "open_po":              open_po,
    "bookings":             bookings,
    "supplier_invoices":    supplier_invoices,
    "gl_bills":             gl_bills,
}

for file_path in [dated_file, latest_file]:
    try:
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            for name, df in SHEETS.items():
                if not df.empty:
                    # Ensure column names are clean
                    df_clean = clean_dataframe_columns(df)
                    df_clean.to_excel(writer, sheet_name=name[:31], index=False)
        print(f"  [OK] Wrote {file_path}")
    except Exception as e:
        print(f"  [ERROR] Failed to write {file_path}: {e}")
        raise

print("\n" + "=" * 80)
print("  BUILD COMPLETE")
print(f"  Archive : {dated_file}")
print(f"  Latest  : {latest_file}")
print("=" * 80)
