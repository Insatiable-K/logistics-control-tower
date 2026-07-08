#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FINAL CORRECT Invoice Compliance ETL Pipeline
Logic:
  - COMPLETE: GL entry exists for category (alphabetic description only)
  - PENDING: GL entry with bill_inv containing "PEND"
  - MISSING: No GL for category + (No Bill OR Bill without PEND)
"""

import pandas as pd
import numpy as np
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_container, clean_date, is_pending
)

print("=" * 80)
print("INVOICE COMPLIANCE ETL - FINAL CORRECT VERSION")
print(f"Run date: {datetime.today().date()}")
print("=" * 80)

# ============================================================================
# STEP 1: LOAD & FILTER SHIPMENT DATA
# ============================================================================
print("\nSTEP 1: Load shipment data and filter to international")
print("-" * 80)

in_transit = standardize_columns(load_html_table(Path("In-Transit List by SIPL.xls")))
inventory = standardize_columns(load_html_table(Path("Inventory In Transit - Detail .xls")))

# Clean containers - ISO 6346 only (international)
in_transit['_container_clean'] = in_transit['container'].apply(clean_container)
inventory['_container_clean'] = inventory['container'].apply(clean_container)

in_transit_intl = in_transit[in_transit['_container_clean'].notna()].copy()

print(f"  IN_TRANSIT total: {len(in_transit)}")
print(f"  International (ISO container): {len(in_transit_intl)}")
print(f"  Removed (domestic): {len(in_transit) - len(in_transit_intl)}")

# ============================================================================
# STEP 2: FILTER BY ETA WINDOW (2-3 days or 7 days from today)
# ============================================================================
print("\nSTEP 2: Filter to ETA window (Port ETA 2-3 days OR Location ETA 7 days)")
print("-" * 80)

today = pd.Timestamp.today().normalize()

in_transit_intl['_port_eta'] = clean_date(in_transit_intl['port_eta'])
in_transit_intl['_location_eta'] = clean_date(in_transit_intl['location_eta'])

# Port ETA: Past or within 2-3 days (use 4 for safety)
port_eta_filter = (
    (in_transit_intl['_port_eta'] <= today + timedelta(days=4)) &
    (in_transit_intl['_port_eta'].notna())
)

# Location ETA: Within 7 days
location_eta_filter = (
    (in_transit_intl['_location_eta'] <= today + timedelta(days=7)) &
    (in_transit_intl['_location_eta'].notna())
)

shipment_master = in_transit_intl[port_eta_filter | location_eta_filter].copy()

print(f"  Port ETA (past or next 4 days): {port_eta_filter.sum()}")
print(f"  Location ETA (next 7 days): {location_eta_filter.sum()}")
print(f"  Total in scope: {len(shipment_master)}")
print(f"  Distinct containers: {shipment_master['_container_clean'].nunique()}")
print(f"  Distinct SIPLs: {shipment_master['sipl'].nunique()}")

shipment_containers = set(shipment_master['_container_clean'].unique())
shipment_sipls = set(shipment_master['sipl'].unique())

# ============================================================================
# STEP 3: LOAD BILLS & FILTER TO SHIPMENT UNIVERSE
# ============================================================================
print("\nSTEP 3: Load Bills and match to shipments")
print("-" * 80)

bills = standardize_columns(load_html_table(Path("Bills.xls")))
bills['_container_clean'] = bills['container'].apply(clean_container)
bills['_is_pending'] = is_pending(bills['bill_inv'])

print(f"  Bills total: {len(bills)}")
print(f"  With valid container: {bills['_container_clean'].notna().sum()}")

# Match to shipment universe
bills_valid = bills[bills['_container_clean'].notna()].copy()

bills_matched = bills_valid[
    (bills_valid['_container_clean'].isin(shipment_containers)) |
    (bills_valid['sipl_inv'].astype(str).str.strip().isin(shipment_sipls))
].copy()

print(f"  Matched to shipments: {len(bills_matched)}")
print(f"  Pending bills (PEND marker): {bills_matched['_is_pending'].sum()}")

# ============================================================================
# STEP 4: LOAD GL & FILTER DESCRIPTIONS (ALPHABETIC ONLY)
# ============================================================================
print("\nSTEP 4: Load GL and filter descriptions (alphabetic only)")
print("-" * 80)

gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

gl_data = pd.concat([
    gl_1313.assign(_gl_source='GL 1313'),
    gl_1275.assign(_gl_source='GL 1275')
], ignore_index=True)

print(f"  GL total: {len(gl_data)}")

# Step 1: Exclude GL entries where invoice='PENDING' (ledger entries, not real invoices)
gl_data_no_pending_inv = gl_data[
    ~(gl_data['invoice'].astype(str).str.strip().str.upper().isin(['PENDING', 'PEND', '']))
].copy()

print(f"  After removing invoice='PENDING' entries: {len(gl_data_no_pending_inv)} (removed {len(gl_data) - len(gl_data_no_pending_inv)} ledger placeholders)")

# Step 2: Filter descriptions: alphabetic only (no numbers)
def has_valid_description(description):
    """
    Check if description:
    1. Has alphabetic chars but no digits
    """
    if pd.isna(description):
        return False
    desc = str(description).strip()
    if not desc:  # Exclude blank/empty descriptions
        return False

    # Has alpha, no digits
    has_alpha = any(c.isalpha() for c in desc)
    has_digit = any(c.isdigit() for c in desc)
    return has_alpha and not has_digit

gl_data_no_pending_inv['_has_valid_desc'] = gl_data_no_pending_inv['description'].apply(has_valid_description)
gl_filtered = gl_data_no_pending_inv[gl_data_no_pending_inv['_has_valid_desc']].copy()

print(f"  After alphabetic-only description filter: {len(gl_filtered)}")
print(f"  Total removed: {len(gl_data) - len(gl_filtered)}")

# Classify GL
def classify_gl_category(description):
    if pd.isna(description):
        return 'Other'
    desc = str(description).upper().strip()

    # Check for exact abbreviations first (most specific)
    if desc in ['OF', 'OI']:  # Ocean Freight abbreviations
        return 'Ocean Freight'
    elif desc in ['CU', 'CA']:  # Customs abbreviations
        return 'Customs'
    elif desc in ['DU']:  # Duty abbreviation
        return 'Duty'
    elif desc in ['DR']:  # Drayage abbreviation
        return 'Drayage'

    # Check for longer text patterns (less specific, but more complete)
    if 'OCEAN FREIGHT' in desc or ('FREIGHT' in desc and 'OCEAN' in desc):
        return 'Ocean Freight'
    elif 'CUSTOM' in desc:
        return 'Customs'
    elif 'DUTY' in desc:
        return 'Duty'
    elif 'DRAYAGE' in desc or 'DRYAGE' in desc or 'CARTAGE' in desc or 'LOCAL' in desc:
        return 'Drayage'
    else:
        return 'Other'

gl_filtered['_category'] = gl_filtered['description'].apply(classify_gl_category)

print(f"  GL by category:")
for cat in ['Ocean Freight', 'Customs', 'Duty', 'Drayage', 'Other']:
    count = (gl_filtered['_category'] == cat).sum()
    if count > 0:
        pct = 100 * count / len(gl_filtered)
        print(f"    {cat}: {count} ({pct:.1f}%)")

# ============================================================================
# STEP 5: MATCH GL TO BILLS & SHIPMENTS
# ============================================================================
print("\nSTEP 5: Match GL to bills and shipments")
print("-" * 80)

# Normalize invoice numbers
bills_matched['_invoice_normalized'] = bills_matched['bill_inv'].astype(str).str.strip().str.upper()
gl_filtered['_invoice_normalized'] = gl_filtered['invoice'].astype(str).str.strip().str.upper()

# Create lookups
bill_to_container = dict(zip(bills_matched['_invoice_normalized'], bills_matched['_container_clean']))
bill_to_sipl = dict(zip(bills_matched['_invoice_normalized'], bills_matched['sipl_inv'].astype(str).str.strip()))

# Map GL
gl_filtered['_gl_container'] = gl_filtered['_invoice_normalized'].map(bill_to_container)
gl_filtered['_gl_sipl'] = gl_filtered['_invoice_normalized'].map(bill_to_sipl)

gl_matched = gl_filtered[gl_filtered['_gl_container'].notna()].copy()

print(f"  GL entries matched to bills: {len(gl_matched)}")
print(f"  Match rate: {100*len(gl_matched)/len(gl_filtered):.1f}%")

# ============================================================================
# STEP 6: SCORE INVOICE COMPLIANCE (COMPLETE/PENDING/MISSING)
# ============================================================================
print("\nSTEP 6: Score compliance per SIPL")
print("-" * 80)

required_categories = ['Ocean Freight', 'Customs', 'Duty', 'Drayage']
compliance_rows = []

for sipl in sorted(shipment_sipls):
    shipment_rows = shipment_master[shipment_master['sipl'] == sipl]
    if len(shipment_rows) == 0:
        continue

    shipment = shipment_rows.iloc[0]
    container = shipment['_container_clean']

    compliance_row = {
        'sipl': sipl,
        'container': container,
        'supplier': shipment.get('supplier', ''),
        'status': shipment.get('status', ''),
        'port_eta': shipment.get('port_eta', ''),
        'location_eta': shipment.get('location_eta', ''),
        'sipl_status': shipment.get('sipl_status', ''),
    }

    missing_cats = []
    pending_cats = []
    complete_cats = []

    # Score each category
    for cat in required_categories:
        # Step 1: Check GL for this category (COMPLETE)
        # GL matches on real bill_inv (not PENDING)
        gl_for_cat = gl_matched[
            (gl_matched['_category'] == cat) &
            ((gl_matched['_gl_container'] == container) | (gl_matched['_gl_sipl'] == sipl))
        ]

        if len(gl_for_cat) > 0:
            compliance_row[f'{cat}_status'] = 'Complete'
            complete_cats.append(cat)
        else:
            # No GL entry for this category
            # Step 2: Check for PENDING marker first (PENDING)
            bills_pending = bills_matched[
                ((bills_matched['_container_clean'] == container) |
                 (bills_matched['sipl_inv'].astype(str).str.strip() == sipl)) &
                bills_matched['_is_pending']  # PENDING placeholder only
            ]

            if len(bills_pending) > 0:
                # PENDING marker exists = awaiting vendor invoice
                compliance_row[f'{cat}_status'] = 'Pending'
                pending_cats.append(cat)
            else:
                # No GL and no PENDING, check for REAL bill
                bills_real = bills_matched[
                    ((bills_matched['_container_clean'] == container) |
                     (bills_matched['sipl_inv'].astype(str).str.strip() == sipl)) &
                    ~bills_matched['_is_pending']  # REAL bills only, no PENDING
                ]

                if len(bills_real) > 0:
                    # Real bill exists but no GL entry yet = MISSING
                    # (bill was entered but not yet posted to GL)
                    compliance_row[f'{cat}_status'] = 'Missing'
                    missing_cats.append(cat)
                else:
                    # Nothing exists = MISSING
                    compliance_row[f'{cat}_status'] = 'Missing'
                    missing_cats.append(cat)

    # Overall status
    if len(missing_cats) > 0:
        compliance_row['overall_status'] = 'Missing'
    elif len(pending_cats) > 0:
        compliance_row['overall_status'] = 'Pending'
    else:
        compliance_row['overall_status'] = 'Complete'

    compliance_row['missing_categories'] = ', '.join(missing_cats) if missing_cats else None
    compliance_row['pending_categories'] = ', '.join(pending_cats) if pending_cats else None

    compliance_rows.append(compliance_row)

invoice_compliance = pd.DataFrame(compliance_rows)

print(f"  SIPLs scored: {len(invoice_compliance)}")

status_dist = invoice_compliance['overall_status'].value_counts()
for status in ['Complete', 'Pending', 'Missing']:
    count = status_dist.get(status, 0)
    pct = 100 * count / len(invoice_compliance) if len(invoice_compliance) > 0 else 0
    print(f"    {status}: {count} ({pct:.1f}%)")

# ============================================================================
# FINAL SUMMARY
# ============================================================================
print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
Shipment Universe (International, ETA window):
  Total: {len(shipment_master)}
  Containers: {len(shipment_containers)}
  SIPLs: {len(shipment_sipls)}

Bills Processing:
  Total: {len(bills)}
  Valid containers: {len(bills_valid)}
  Matched to shipments: {len(bills_matched)}

GL Processing (Alphabetic descriptions only):
  Total: {len(gl_data)}
  Alphabetic only: {len(gl_filtered)}
  Matched to bills/shipments: {len(gl_matched)}

Invoice Compliance:
  Complete: {(invoice_compliance['overall_status'] == 'Complete').sum()}
  Pending: {(invoice_compliance['overall_status'] == 'Pending').sum()}
  Missing: {(invoice_compliance['overall_status'] == 'Missing').sum()}
""")

# Save results
invoice_compliance.to_csv('invoice_compliance_v3_final.csv', index=False)
print("[OK] Saved to invoice_compliance_v3_final.csv")
