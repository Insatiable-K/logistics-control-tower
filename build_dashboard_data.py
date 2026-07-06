# -*- coding: utf-8 -*-
"""
build_dashboard_data.py — Logistics Control Tower ETL + Business Rule Engine
Architectural Surfaces

Reads all 10 raw source files directly (no SQL round-trip), cleans them,
builds the container/SIPL shipment master, and builds the invoice
compliance engine. Writes a single dashboard_data.xlsx consumed by
app_cloud.py (via logic_cloud.py — no business logic lives in the app).

CONFIRMED BUSINESS DECISIONS THIS SCRIPT ENCODES (from stakeholder review):
  1. Scope = containers currently "on the water" (Open PO / In-Transit).
     Inventory_Received.xls is intentionally NOT used yet — it only covers
     a partial 20-day window (May 1-20, 2026), not full history, and
     historical-receipt reporting is an explicitly deferred phase.
  2. Bill Split compliance is scored PER-SIPL: each split line must
     independently cover its own SIPL. A container is only "Complete" for
     a category once EVERY one of its SIPLs is covered for that category.
  3. Accessorial charges (Detention, Demurrage, Per Diem, Chassis, Exam,
     Terminal, Admin Fee, Misc, Credit Memo) are tracked separately as a
     cost rollup. They NEVER affect Complete/Pending/Missing status.
  4. Only the Bhargava and Sanket tabs in Freight_Bills_Processed_2026.xlsm
     are authoritative for invoice-type classification. Aswin/Basheer/
     Harish/Mohan/Raheem are >99% blank on Invoice Type and are excluded.

DATA LIMITATION THIS SCRIPT IS HONEST ABOUT:
  A "PENDING" bill in Bills.xls has not yet posted to GL, so it cannot be
  matched to a GL description to learn its category. We only know its
  category when Bhargava/Sanket manually pre-tagged it. Uncategorized
  pending bills are surfaced as a separate "has_pending_activity" signal
  at the container/SIPL level — they are NOT silently assumed to satisfy
  any one of the 4 required categories, because that would fabricate
  precision the data doesn't support.

RUN:
    python build_dashboard_data.py
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

SOURCE_DIR = Path(__file__).parent / "source_files"
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = pd.Timestamp.today().normalize()

REQUIRED_CATEGORIES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]
ACCESSORIAL_LABEL = "ACCESSORIAL"

FILES = {
    "open_po":            SOURCE_DIR / "Open_PO_List.xls",
    "in_transit":         SOURCE_DIR / "InTransit_List_by_SIPL.xls",
    "inventory_intransit": SOURCE_DIR / "Inventory_In_Transit__Detail_.xls",
    "supplier_invoices":  SOURCE_DIR / "Supplier_Invoices.xls",
    "account_1275":       SOURCE_DIR / "Account_Register__1275__Capitalized_Inventory_Freight.xls",
    "account_1313":       SOURCE_DIR / "Account_Register__1313__Prepaid_Container_Freight.xls",
    "bills":              SOURCE_DIR / "Bills.xls",
    "bookings":           SOURCE_DIR / "Bookings_Tracker.xlsx",
    "freight_processed":  SOURCE_DIR / "Freight_Bills_Processed__2026.xlsm",
}

print("=" * 70)
print("  LOGISTICS CONTROL TOWER — DASHBOARD DATA BUILD")
print(f"  Run date: {TODAY.date()}")
print("=" * 70)

# =============================================================================
# STEP 1 — LOAD + CLEAN EACH SOURCE
# =============================================================================
print("\n--- STEP 1: LOAD + CLEAN SOURCES ---")

# --- Open PO List (line-item grain; PO# repeats across product lines) ------
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

# --- In-Transit List by SIPL (SIPL grain; current execution status) --------
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

# --- Inventory In-Transit Detail (SKU grain within a SIPL) ------------------
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
print(f"  inventory_intransit  : {inventory_intransit.shape}"
      f"  (ship_b_l_date non-null: {inventory_intransit['ship_b_l_date'].notna().sum():,})")

# --- Supplier Invoices -> shipment_mapping (structural PO/SIPL/Container) --
# NOTE: Supplier_Invoices.xls is the MERCHANDISE invoice register (what we
# owe the *supplier*), not a freight/logistics document. We only use its
# PO#/SIPL#/Container# columns here as a relationship crosswalk — its
# Amount/Balance Due fields are NOT part of the freight compliance engine.
supplier_invoices = standardize_columns(load_html_table(FILES["supplier_invoices"]))
supplier_invoices["container"] = supplier_invoices["container"].apply(clean_container)
supplier_invoices["sipl"] = clean_text(supplier_invoices.get("sipl"))
supplier_invoices["p_o"] = clean_text(supplier_invoices.get("p_o"))
print(f"  supplier_invoices    : {supplier_invoices.shape}")

# --- GL Registers 1275 + 1313 -----------------------------------------------
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

# CRITICAL FILTER: only Type == 'Bill' rows are logistics invoices.
# Type == 'SupplierInvoice' rows are merchandise cost capitalization
# (Description is literally "PO# 12345") — including them would corrupt
# the freight category classification. Confirmed via discovery.
gl_freight = gl_bills[gl_bills["type"] == "Bill"].copy()
gl_supplier_cost = gl_bills[gl_bills["type"] == "SupplierInvoice"].copy()
print(f"    -> gl 'Bill' rows (freight, used for compliance) : {len(gl_freight):,}")
print(f"    -> gl 'SupplierInvoice' rows (merch cost, excluded from compliance): {len(gl_supplier_cost):,}")

# --- Bills.xls (Invoice# <-> SIPL/Container bridge; ~86k rows) -------------
bills = standardize_columns(load_html_table(FILES["bills"]))
bills = bills.rename(columns={
    "bill_inv": "bill_inv", "sipl_inv": "sipl", "container": "container",
    "non_inventory_vendor": "vendor",
})
bills["container"] = bills["container"].apply(clean_container)
bills["sipl"] = clean_text(bills.get("sipl"))
bills["bill_inv"] = clean_text(bills.get("bill_inv")).str.upper()
bills["vendor"] = clean_text(bills.get("vendor"))
for col in ["invoice_dt", "due_date", "sipl_inv_dt", "sipl_ship_dt"]:
    if col in bills.columns:
        bills[col] = clean_date(bills[col])
for col in ["amount", "balance_due", "sipl_amount"]:
    if col in bills.columns:
        bills[col] = clean_currency(bills[col])
bills["is_pending"] = is_pending(bills["bill_inv"])
print(f"  bills                : {bills.shape}  (pending: {bills['is_pending'].sum():,})")

# --- Bookings Tracker (event-log grain) -------------------------------------
bookings = standardize_columns(pd.read_excel(FILES["bookings"], sheet_name="Event_Log"))
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

# --- Freight Bills Processed (Bhargava/Sanket) is NOT used for invoice
# category classification. UPDATED BUSINESS DECISION: rely on GL 1275/1313
# as the sole source of category truth — these are hand-maintained audit
# logs, not a system of record, and the stakeholder decided GL description
# is more trustworthy than manual tagging. Kept available below ONLY as an
# optional cross-check sheet (qc_processor_crosscheck), never as an input
# to invoice_events / invoice_compliance.
def load_processor_tab(sheet_name, container_col):
    df = pd.read_excel(FILES["freight_processed"], sheet_name=sheet_name)
    df = standardize_columns(df)
    container_col = standardize_columns(pd.DataFrame(columns=[container_col])).columns[0]
    df["container"] = df[container_col].apply(clean_container)
    df["sipl"] = clean_text(df.get("sipl"))
    df["invoice"] = clean_text(df.get("invoice")).str.upper()
    df["invoice_type"] = clean_text(df.get("invoice_type"))
    df["gl_posted"] = clean_numeric(df.get("gl_posted"))
    df["amount"] = clean_currency(df.get("amount"))
    df["invoice_date"] = clean_date(df.get("invoice_date"))
    df["is_pending"] = is_pending(df["invoice"])
    df["processor"] = sheet_name
    keep = ["processor", "sipl", "container", "invoice", "invoice_type",
            "gl_posted", "amount", "invoice_date", "is_pending"]
    return df[keep]

processor_bhargava = load_processor_tab("Bhargava", "Container")
processor_sanket = load_processor_tab("Sanket", "Container ")  # trailing space in source
qc_processor_crosscheck = pd.concat([processor_bhargava, processor_sanket], ignore_index=True)
qc_processor_crosscheck = qc_processor_crosscheck[
    qc_processor_crosscheck["invoice_type"].notna() | qc_processor_crosscheck["is_pending"]
].copy()
print(f"  freight_processed (Bhargava+Sanket, cross-check ONLY, not used for compliance) : "
      f"{qc_processor_crosscheck.shape}")

print("\n[OK] All sources loaded and cleaned")

# =============================================================================
# STEP 2 — BUILD THE SHIPMENT MAPPING MASTER (Container <-> SIPL <-> PO)
# =============================================================================
# There is no single raw file that authoritatively links Container, SIPL,
# and PO together — this was assumed by the domain skill but does not exist
# among the source files (confirmed during discovery). It must be derived
# by unioning every (container, sipl, po) triple observed across the four
# sources that carry at least two of these three keys.
print("\n--- STEP 2: BUILD SHIPMENT MAPPING (Container <-> SIPL <-> PO) ---")

pairs = []

sm1 = supplier_invoices[["container", "sipl", "p_o"]].rename(columns={"p_o": "po_number"})
pairs.append(sm1)

sm2 = open_po[["container", "po"]].rename(columns={"po": "po_number"})
sm2["sipl"] = np.nan
pairs.append(sm2[["container", "sipl", "po_number"]])

sm3 = bookings[["container_id", "po_number"]].rename(columns={"container_id": "container"})
sm3["po_number"] = sm3["po_number"].astype(str)
sm3["sipl"] = np.nan
pairs.append(sm3[["container", "sipl", "po_number"]])

sm4 = bills[["container", "sipl"]].copy()
sm4["po_number"] = np.nan
pairs.append(sm4[["container", "sipl", "po_number"]])

shipment_mapping = pd.concat(pairs, ignore_index=True)
shipment_mapping["po_number"] = shipment_mapping["po_number"].astype(str).replace({"nan": np.nan, "<NA>": np.nan})
shipment_mapping = shipment_mapping[
    shipment_mapping["container"].notna() | shipment_mapping["sipl"].notna()
].drop_duplicates().reset_index(drop=True)
# NOTE: renamed to container_id/sipl_number here (not container/sipl) because
# logic_cloud.py's load_shipment_mapping(), load_bookings(), and
# build_execution_master() all already expect this exact contract in
# multiple places. Renaming once here is far lower-risk than changing every
# consumer, and keeps one single naming convention for this specific table.
shipment_mapping = shipment_mapping.rename(columns={"container": "container_id", "sipl": "sipl_number"})

print(f"  shipment_mapping     : {shipment_mapping.shape}  "
      f"(distinct containers: {shipment_mapping['container_id'].nunique():,}, "
      f"distinct SIPLs: {shipment_mapping['sipl_number'].nunique():,})")

# One SIPL -> one container is the expected cardinality (confirmed in
# discovery: 99.8% of InTransit_SIPL rows are unique on SIPL#). Flag any
# SIPL that maps to more than one container as a data-quality exception
# rather than silently picking one.
sipl_container_counts = (
    shipment_mapping.dropna(subset=["sipl_number", "container_id"])
    .drop_duplicates(subset=["sipl_number", "container_id"])
    .groupby("sipl_number")["container_id"].nunique()
)
sipl_multi_container = sipl_container_counts[sipl_container_counts > 1]
if len(sipl_multi_container):
    print(f"  [WARN] {len(sipl_multi_container)} SIPL(s) map to more than one container "
          f"— see qc_exceptions sheet")

# =============================================================================
# STEP 3 — BUILD THE ACTIVE SHIPMENT SCOPE ("on the water")
# =============================================================================
# Per stakeholder decision: scope = currently open/in-transit shipments.
# Inventory_Received.xls is intentionally excluded (partial-history file,
# historical-receipt reporting deferred).
print("\n--- STEP 3: BUILD ACTIVE SHIPMENT (SIPL) MASTER ---")

sipl_master = in_transit.copy()
sipl_master = sipl_master.rename(columns={
    "fr_forwarder": "freight_forwarder",
})

# Attach PO number(s) per SIPL from the shipment mapping (a SIPL can have
# more than one PO if multiple orders were consolidated onto one shipment).
po_by_sipl = (
    shipment_mapping.dropna(subset=["sipl_number", "po_number"])
    .groupby("sipl_number")["po_number"]
    .apply(lambda s: ", ".join(sorted(set(s))))
    .rename("po_numbers")
    .rename_axis("sipl")
)
sipl_master = sipl_master.merge(po_by_sipl, on="sipl", how="left")

# Operational priority bucket, based on Port ETA vs today.
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

print(f"  sipl_master          : {sipl_master.shape}")
print(f"  Operational priority breakdown:")
print(sipl_master["operational_priority"].value_counts().to_string())

# =============================================================================
# STEP 4 — INVOICE CATEGORY NORMALIZATION
# =============================================================================
print("\n--- STEP 4: INVOICE CATEGORY NORMALIZATION ---")

try:
    from rapidfuzz import process, fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:
    HAVE_RAPIDFUZZ = False
    print("  [WARN] rapidfuzz not installed — exact/substring rules only, no fuzzy fallback")


def normalize_category(text):
    """
    Maps a free-text invoice/description label to one of the 4 required
    compliance categories, or ACCESSORIAL for real-but-non-required charges
    (Detention, Demurrage, Per Diem, Chassis, Exam, Terminal, Admin Fee,
    Misc, Credit Memo — confirmed vocabulary from Freight_Bills_Processed),
    or None if it can't be determined at all (e.g. a bare PO# reference).
    Per stakeholder decision: ACCESSORIAL is tracked for cost visibility
    only and never gates Complete/Pending/Missing status.
    """
    if pd.isna(text):
        return None
    t = str(text).strip().upper()
    if t == "" or t in {"MISC", "PO", "AMS", "ISF", "ISC"} or re.match(r"^PO\s*#", t):
        # AMS/ISF/ISC are real accessorial-adjacent filing fees, not one of
        # the 4 required categories and not worth a dedicated bucket —
        # grouped into ACCESSORIAL below via the catch-all, not here.
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
        return None  # bare PO reference in GL description — not a freight bill category
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


# --- GL 'Bill' rows classified by description (the sole source of truth) --
gl_freight["category"] = gl_freight["description"].apply(normalize_category)
gl_lookup = (
    gl_freight[gl_freight["category"].notna()]
    [["invoice", "category", "gl_account", "date", "party"]]
    .drop_duplicates(subset=["invoice", "category"])
)

# --- Bills.xls bridges Invoice# -> Container/SIPL for GL-classified bills -
bills_confirmed = bills[~bills["is_pending"]].copy()
gl_matched = bills_confirmed.merge(
    gl_lookup, left_on="bill_inv", right_on="invoice", how="inner"
)
gl_matched = gl_matched.rename(columns={"bill_inv": "invoice_number", "invoice_dt": "invoice_date"})
gl_matched["source_tier"] = "GL_CONFIRMED"
gl_matched["is_pending"] = False
print(f"  GL-confirmed events (Bills.xls matched to GL by invoice#) : {len(gl_matched):,} "
      f"of {len(bills_confirmed):,} confirmed bills "
      f"({len(gl_matched) / max(len(bills_confirmed), 1):.1%} match rate)")

# --- Uncategorized pending activity (context only, not compliance) --------
# A PENDING bill in Bills.xls has not posted to GL, so we cannot learn its
# category from a GL description match. We do NOT guess which of the 4
# categories it will eventually satisfy — that would fabricate precision
# the data doesn't support. Instead this becomes a separate signal: "we
# know something is in progress for this container/SIPL," surfaced at the
# overall_status level (Pending), not attributed to a specific category.
pending_activity = bills[bills["is_pending"]][["container", "sipl", "bill_inv", "vendor", "amount"]].copy()
print(f"  Uncategorized pending activity (context signal only) : {len(pending_activity):,}")

# =============================================================================
# STEP 5 — INVOICE EVENT LOG (GL-confirmed only)
# =============================================================================
print("\n--- STEP 5: BUILD INVOICE EVENT LOG ---")

event_cols = ["container", "sipl", "category", "is_pending", "amount",
              "source_tier", "invoice_number"]
invoice_events = gl_matched.reindex(columns=event_cols)
invoice_events = invoice_events[invoice_events["category"].notna()].reset_index(drop=True)
print(f"  invoice_events (GL-confirmed) : {invoice_events.shape}")
print(invoice_events["category"].value_counts().to_string())

# =============================================================================
# STEP 6 — PER-SIPL INVOICE COMPLIANCE  (Complete / Pending / Missing)
# =============================================================================
# Stakeholder decision: Bill Split invoices are scored PER-SIPL. Each SIPL
# must independently have its own evidence for each required category —
# an invoice covering a sibling SIPL on the same container does not count.
#
# Without a category-tagged processor log, "Pending" can no longer be
# assigned to a SPECIFIC category (we don't know which of the 4 categories
# an uncategorized pending bill will eventually satisfy). Category status
# is therefore binary — Complete / Missing — and Pending is applied at the
# overall_status level when pending activity exists for a SIPL/container
# that still has at least one Missing category.
print("\n--- STEP 6: PER-SIPL COMPLIANCE MATRIX ---")

required_events = invoice_events[invoice_events["category"].isin(REQUIRED_CATEGORIES)].copy()
accessorial_events = invoice_events[invoice_events["category"] == ACCESSORIAL_LABEL].copy()

complete_by_sipl = (
    required_events[required_events["sipl"].notna()]
    .drop_duplicates(subset=["sipl", "category"])
    .assign(flag=1)
    .pivot_table(index="sipl", columns="category", values="flag", fill_value=0)
    .reindex(columns=REQUIRED_CATEGORIES, fill_value=0)
)

# Explicit pending coverage per (sipl, category) — only from Tier-1, where
# the processor manually pre-tagged a placeholder's eventual category.
pending = required_events[required_events["is_pending"]]
pending_by_sipl = (
    pending[pending["sipl"].notna()]
    .drop_duplicates(subset=["sipl", "category"])
    .assign(flag=1)
    .pivot_table(index="sipl", columns="category", values="flag", fill_value=0)
    .reindex(columns=REQUIRED_CATEGORIES, fill_value=0)
)

# Container-level events with no SIPL attached (event has a container but
# the source row didn't carry a SIPL). We surface these as a distinct
# "container_level_unattributed" flag rather than guessing which SIPL(s)
# on that container they belong to — silently crediting every SIPL on the
# container would contradict the per-SIPL scoring decision.
unattributed = required_events[required_events["sipl"].isna() & required_events["container"].notna()]
unattributed_by_container = (
    unattributed.drop_duplicates(subset=["container", "category"])
    .assign(flag=1)
    .pivot_table(index="container", columns="category", values="flag", fill_value=0)
    .reindex(columns=REQUIRED_CATEGORIES, fill_value=0)
)

# Uncategorized pending activity — a signal, not a category-specific match.
pending_activity_siplset = set(pending_activity["sipl"].dropna())
pending_activity_containerset = set(pending_activity["container"].dropna())

# --- Assemble the compliance table, one row per active SIPL ----------------
rows = []
for _, s in sipl_master.iterrows():
    sipl_id = s["sipl"]
    container_id = s["container"]
    row = {
        "sipl": sipl_id,
        "container": container_id,
        "po_numbers": s.get("po_numbers"),
        "supplier": s.get("supplier"),
        "freight_forwarder": s.get("freight_forwarder"),
        "port_eta": s.get("port_eta"),
        "days_to_port_eta": s.get("days_to_port_eta"),
        "operational_priority": s.get("operational_priority"),
    }
    comp_row = complete_by_sipl.loc[sipl_id] if sipl_id in complete_by_sipl.index else pd.Series(0, index=REQUIRED_CATEGORIES)
    unattr_row = unattributed_by_container.loc[container_id] if container_id in unattributed_by_container.index else pd.Series(0, index=REQUIRED_CATEGORIES)

    missing_cats = []
    for cat in REQUIRED_CATEGORIES:
        if comp_row[cat] == 1:
            row[f"{cat}_status"] = "Complete"
        else:
            row[f"{cat}_status"] = "Missing"
            missing_cats.append(cat)
        row[f"{cat}_container_level_unattributed_evidence"] = bool(unattr_row[cat] == 1)

    has_pending_activity = (
        sipl_id in pending_activity_siplset or container_id in pending_activity_containerset
    )
    row["missing_categories"] = ", ".join(missing_cats)
    row["has_uncategorized_pending_activity"] = has_pending_activity

    if not missing_cats:
        row["overall_status"] = "Complete"
    elif has_pending_activity:
        # We know a bill has been submitted and hasn't posted yet, but per
        # the GL-only decision we can't say which missing category it will
        # resolve. Pending here means "activity in progress," not "this
        # specific category is pending" — see missing_categories for what
        # is still confirmed outstanding regardless.
        row["overall_status"] = "Pending"
    else:
        row["overall_status"] = "Missing"
    rows.append(row)

invoice_compliance = pd.DataFrame(rows)
print(f"  invoice_compliance (per-SIPL) : {invoice_compliance.shape}")
print(invoice_compliance["overall_status"].value_counts().to_string())
print("\n  Missing-category breakdown (containers past ETA or arriving <=3 days):")
urgent = invoice_compliance[invoice_compliance["operational_priority"].isin(
    ["Past ETA", "Arriving Today", "Next 3 Days"])]
print(f"    {len(urgent)} SIPLs in urgent window, "
      f"{(urgent['overall_status'] == 'Missing').sum()} with at least one Missing category")

# =============================================================================
# STEP 7 — ACCESSORIAL COST ROLLUP (tracked separately, never gates status)
# =============================================================================
print("\n--- STEP 7: ACCESSORIAL COST ROLLUP ---")

accessorial_rollup = (
    accessorial_events.groupby(["container", "sipl"], dropna=False)
    .agg(accessorial_charge_count=("amount", "count"),
         accessorial_total_amount=("amount", "sum"))
    .reset_index()
)
print(f"  accessorial_rollup   : {accessorial_rollup.shape}  "
      f"(total: ${accessorial_rollup['accessorial_total_amount'].sum():,.2f})")

# =============================================================================
# STEP 8 — CONTAINER-LEVEL ROLLUP
# =============================================================================
# The compliance matrix above is per-SIPL (the correct grain for scoring,
# per stakeholder decision). Most dashboard views are container-centric, so
# this rolls per-SIPL results up: a category is only "Complete" for a
# container once EVERY SIPL on it is Complete for that category. If any
# SIPL is Missing a category, the container shows Missing for that
# category with a drill-down to which SIPL(s) caused it.
print("\n--- STEP 8: CONTAINER-LEVEL ROLLUP ---")

def rollup_status(statuses):
    if (statuses == "Missing").any():
        return "Missing"
    if (statuses == "Pending").any():
        return "Pending"
    return "Complete"

container_rows = []
for container_id, grp in invoice_compliance.groupby("container", dropna=False):
    row = {
        "container": container_id,
        "sipl_count": len(grp),
        "sipls": ", ".join(sorted(grp["sipl"].dropna().astype(str))),
        "operational_priority": grp["operational_priority"].iloc[0] if grp["operational_priority"].nunique() == 1 else "Mixed",
        "earliest_port_eta": grp["port_eta"].min(),
    }
    missing_cats = []
    for cat in REQUIRED_CATEGORIES:
        status = rollup_status(grp[f"{cat}_status"])
        row[f"{cat}_status"] = status
        if status == "Missing":
            offending = grp.loc[grp[f"{cat}_status"] == "Missing", "sipl"].dropna().tolist()
            missing_cats.append(f"{cat} (SIPL: {', '.join(offending)})")
    row["missing_detail"] = "; ".join(missing_cats)
    row["overall_status"] = rollup_status(grp["overall_status"])
    container_rows.append(row)

container_compliance = pd.DataFrame(container_rows)
acc_by_container = accessorial_rollup.groupby("container")["accessorial_total_amount"].sum().rename("accessorial_total_amount")
container_compliance = container_compliance.merge(acc_by_container, on="container", how="left")
container_compliance["accessorial_total_amount"] = container_compliance["accessorial_total_amount"].fillna(0)

print(f"  container_compliance : {container_compliance.shape}")
print(container_compliance["overall_status"].value_counts().to_string())

# =============================================================================
# STEP 9 — QC EXCEPTIONS
# =============================================================================
print("\n--- STEP 9: QC EXCEPTIONS ---")

qc_exceptions = []
for sipl_id, containers in sipl_multi_container.items():
    qc_exceptions.append({
        "exception_type": "SIPL_MULTIPLE_CONTAINERS",
        "detail": f"SIPL {sipl_id} maps to {containers} distinct containers",
    })

# Bhargava/Sanket rows with a container that fails ISO extraction entirely
bad_container_tier1 = qc_processor_crosscheck[
    qc_processor_crosscheck["container"].isna() & qc_processor_crosscheck["invoice"].notna()
]
for _, r in bad_container_tier1.head(200).iterrows():
    qc_exceptions.append({
        "exception_type": "TIER1_UNRESOLVED_CONTAINER",
        "detail": f"Processor {r['processor']} invoice {r['invoice']} has no extractable container ID",
    })

qc_exceptions_df = pd.DataFrame(qc_exceptions)
print(f"  qc_exceptions        : {qc_exceptions_df.shape}")

# =============================================================================
# STEP 10 — WRITE DASHBOARD_DATA.XLSX
# =============================================================================
print("\n--- STEP 10: WRITE OUTPUT ---")

today_str = TODAY.strftime("%Y-%m-%d")
dated_file = OUTPUT_DIR / f"dashboard_data_{today_str}.xlsx"
latest_file = OUTPUT_DIR / "dashboard_data.xlsx"

SHEETS = {
    "open_po":              open_po,
    "in_transit":           in_transit,
    "inventory_intransit":  inventory_intransit,
    "bookings":             bookings,
    "shipment_mapping":     shipment_mapping,
    "gl_bills":             gl_bills,
    "bills":                bills,
    "invoice_events":       invoice_events,
    "invoice_compliance":   invoice_compliance,
    "container_compliance": container_compliance,
    "accessorial_rollup":   accessorial_rollup,
    "qc_exceptions":        qc_exceptions_df,
    "qc_processor_crosscheck": qc_processor_crosscheck,
}

for file_path in [dated_file, latest_file]:
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        for name, df in SHEETS.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    print(f"  Wrote {file_path}")

print("\n" + "=" * 70)
print("  BUILD COMPLETE")
print(f"  Archive : {dated_file}")
print(f"  Latest  : {latest_file}")
print("=" * 70)
