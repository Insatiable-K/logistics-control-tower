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

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_currency, clean_date,
    clean_numeric, clean_text, clean_container, is_pending,
    REQUIRED_CATEGORIES,
    clean_bills_dataframe, clean_gl_dataframe, score_container_invoice_compliance,
)

# =============================================================================
# CONFIG
# =============================================================================

SOURCE_DIR = Path(__file__).parent
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = pd.Timestamp.today().normalize()

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

print("  Operational priority breakdown:")
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

print("  Bills matched:")
for match_type, count in match_summary.items():
    print(f"    {match_type}: {count:,}")
total_matched = match_summary['matched_by_sipl'] + match_summary['matched_by_container']
print(f"\n  Total matched: {total_matched:,} of {len(bills):,}")
print(f"  Match rate: {total_matched / len(bills):.1%}")

if len(unmatched_bills_df) > 0:
    print("\n  Top unmatched reasons:")
    print(unmatched_bills_df["reason"].value_counts().head(10).to_string())

# =============================================================================
# STEP 5-6: CONTAINER-LEVEL INVOICE COMPLIANCE (verified engine)
# =============================================================================
# Reuses score_container_invoice_compliance() -- the same engine verified
# and shipped in logistics_dashboard.py's Invoice Compliance tab this
# session. Replaces the old 3-layer SIPL-level engine because its Layer 3
# ("elimination inference") assigned a category to a leftover unmatched
# bill purely because a category slot was still open -- no vendor
# evidence, just a guess. Confirmed with the business: Pending must be
# backed by real evidence (a bill actually on file, ideally
# vendor-attributed via historical GL billing pattern); Missing must only
# apply when a container has genuinely nothing on file at all. The new
# engine also fixes a bug the old one didn't hit at SIPL grain: a single
# GL invoice number can legitimately confirm more than one category
# (1,075 of 2,817 classified GL invoices do), so Complete detection must
# not collapse to one category per invoice.
#
# Bills/costs apply to the physical container, not to one specific SIPL
# riding on it, so this scores at CONTAINER grain, then broadcasts each
# container's per-category status to every SIPL sharing it. The SIPL-grain
# OUTPUT schema below is unchanged from the old engine on purpose --
# logic_cloud.py / app_cloud.py's Tab 3 consume it as-is.
print("\n--- STEP 5-6: BUILD CONTAINER-LEVEL INVOICE COMPLIANCE ---")

bills_for_compliance, _, _ = clean_bills_dataframe(load_html_table(FILES["bills"]))
gl_for_compliance = clean_gl_dataframe(
    load_html_table(FILES["account_1275"]),
    load_html_table(FILES["account_1313"]),
)

tracked_containers = sorted(sipl_master["container"].dropna().unique())
compliance_result = score_container_invoice_compliance(bills_for_compliance, gl_for_compliance, tracked_containers)
compliance_detail = compliance_result["compliance_detail"]      # container, category, status, evidence
pending_bills_detail = compliance_result["pending_bills"]       # container, bill_inv, vendor, amount, invoice_dt, inferred_category

print(f"  Containers scored: {len(tracked_containers):,}")
print(compliance_detail.groupby(["category", "status"]).size().unstack(fill_value=0).to_string())

container_status = compliance_detail.set_index(["container", "category"])["status"].to_dict()

# Vendors to follow up: every distinct vendor with an unresolved bill on
# that container (real evidence of who to chase) -- a container can have
# more than one pending bill from more than one vendor.
pending_vendors_by_container = (
    pending_bills_detail.groupby("container")["vendor"]
    .apply(lambda s: ", ".join(sorted(set(str(v).strip() for v in s.dropna() if str(v).strip()))))
    .to_dict()
)

# Build final invoice_compliance dataframe
print("  Building final invoice_compliance dataframe...")
invoice_compliance_rows = []

for _, sipl_row in sipl_master.iterrows():
    sipl_id = str(sipl_row["sipl"]).strip() if pd.notna(sipl_row["sipl"]) else None
    if not sipl_id:
        continue

    container_id = sipl_row["container"]

    # Arrival status from days_to_port_eta
    days_eta = sipl_row.get("days_to_port_eta")
    if pd.isna(days_eta):
        arrival_status = "Unknown"
    elif days_eta < 0:
        arrival_status = "Past ETA"
    elif days_eta == 0:
        arrival_status = "Today"
    else:
        arrival_status = "Approaching"

    # Initialize row with base columns (must include all expected by logic_cloud.py)
    row = {
        "sipl": sipl_id,
        "container": container_id,
        "po_numbers": sipl_row.get("po_numbers", ""),
        "freight_forwarder": sipl_row.get("freight_forwarder"),
        "destination": sipl_row.get("ship_to_location"),
        "port_eta": sipl_row.get("port_eta"),
        "location_eta": sipl_row.get("location_eta"),
        "days_to_port_eta": sipl_row.get("days_to_port_eta"),
        "arrival_status": arrival_status,
    }

    # Score each required category via the container-level lookup built
    # above (Complete = GL match; Pending = a bill is genuinely in motion
    # on this container, either vendor-attributed or not; Missing = zero
    # bills of any kind on this container for this category). A SIPL with
    # no container_id can't be correlated to any bill and defaults to
    # Missing across the board, same as it would with no evidence at all.
    missing_cats = []
    pending_cats = []

    for cat in REQUIRED_CATEGORIES:
        status = container_status.get((container_id, cat), "Missing")
        row[f"{cat}_status"] = status
        if status == "Missing":
            missing_cats.append(cat)
        elif status == "Pending":
            pending_cats.append(cat)

    # Summary columns
    row["missing_categories"] = ", ".join(missing_cats) if missing_cats else ""
    row["pending_categories"] = ", ".join(pending_cats) if pending_cats else ""
    row["vendor_to_follow_up"] = pending_vendors_by_container.get(container_id, "")

    # Overall status
    if missing_cats:
        row["overall_status"] = "Missing"
    elif pending_cats:
        row["overall_status"] = "Pending"
    else:
        row["overall_status"] = "Complete"

    invoice_compliance_rows.append(row)

invoice_compliance = pd.DataFrame(invoice_compliance_rows)
print(f"  invoice_compliance: {invoice_compliance.shape}")
print(f"    Complete: {(invoice_compliance['overall_status'] == 'Complete').sum()}")
print(f"    Pending: {(invoice_compliance['overall_status'] == 'Pending').sum()}")
print(f"    Missing: {(invoice_compliance['overall_status'] == 'Missing').sum()}")

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
    "metric": "Container x Category Slots -> Complete (GL Confirmed)",
    "count": int((compliance_detail["status"] == "Complete").sum()),
})
audit_rows.append({
    "metric": "Bills Unresolved (Pending -- placeholder or not yet GL-posted)",
    "count": len(pending_bills_detail),
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
