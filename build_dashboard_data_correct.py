#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CORRECT Invoice Compliance ETL Pipeline
Focus: International ocean freight only, Port ETA/Location ETA filtering
"""

import pandas as pd
import numpy as np
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_container, clean_date,
    is_pending
)

print("=" * 80)
print("INVOICE COMPLIANCE ETL - CORRECT LOGIC")
print(f"Run date: {datetime.today().date()}")
print("=" * 80)

# ============================================================================
# STEP 1: LOAD AND CLEAN SHIPMENT DATA
# ============================================================================
print("\nSTEP 1: Load and filter to international ocean freight")
print("-" * 80)

in_transit = standardize_columns(load_html_table(Path("In-Transit List by SIPL.xls")))
inventory = standardize_columns(load_html_table(Path("Inventory In Transit - Detail .xls")))

# Clean containers - STRICT ISO 6346 validation
in_transit['_container_clean'] = in_transit['container'].apply(clean_container)
inventory['_container_clean'] = inventory['container'].apply(clean_container)

# Filter IN_TRANSIT to international (ISO 6346 containers only)
in_transit_intl = in_transit[in_transit['_container_clean'].notna()].copy()

print(f"  IN_TRANSIT total: {len(in_transit)}")
print(f"  After ISO container filter: {len(in_transit_intl)} (removed {len(in_transit) - len(in_transit_intl)} domestic)")

# ============================================================================
# STEP 2: FILTER TO PORT ETA / LOCATION ETA TIMEFRAME
# ============================================================================
print("\nSTEP 2: Filter by arrival timeframe (Past ETA or next 3-4 days, OR location_eta next 7 days)")
print("-" * 80)

today = pd.Timestamp.today().normalize()

# Clean dates
in_transit_intl['_port_eta'] = clean_date(in_transit_intl['port_eta'])
in_transit_intl['_location_eta'] = clean_date(in_transit_intl['location_eta'])
in_transit_intl['_eta_date'] = clean_date(in_transit_intl['eta_date'])

# Filter criteria:
# 1. Port ETA: Past or within 3-4 days (use 4 days for safety)
# 2. Location ETA: Within 7 days
port_eta_filter = (
    (in_transit_intl['_port_eta'] <= today + timedelta(days=4)) &
    (in_transit_intl['_port_eta'].notna())
)

location_eta_filter = (
    (in_transit_intl['_location_eta'] <= today + timedelta(days=7)) &
    (in_transit_intl['_location_eta'].notna())
)

shipment_master = in_transit_intl[port_eta_filter | location_eta_filter].copy()

print(f"  Port ETA filter (past or next 4 days): {port_eta_filter.sum()}")
print(f"  Location ETA filter (next 7 days): {location_eta_filter.sum()}")
print(f"  Total shipments in scope: {len(shipment_master)}")
print(f"  Distinct containers: {shipment_master['_container_clean'].nunique()}")
print(f"  Distinct SIPLs: {shipment_master['sipl'].nunique()}")

# Build shipment universe
shipment_containers = set(shipment_master['_container_clean'].unique())
shipment_sipls = set(shipment_master['sipl'].unique())

print(f"\nOperational scope:")
print(f"  Containers to track: {len(shipment_containers)}")
print(f"  SIPLs to score: {len(shipment_sipls)}")

# ============================================================================
# STEP 3: LOAD AND FILTER BILLS
# ============================================================================
print("\nSTEP 3: Load Bills and filter to operational scope")
print("-" * 80)

bills = standardize_columns(load_html_table(Path("Bills.xls")))

# Clean container and SIPL in bills
bills['_container_clean'] = bills['container'].apply(clean_container)

# Audit: containers in bills
print(f"  Bills total: {len(bills)}")
print(f"  Bills with valid ISO container: {bills['_container_clean'].notna().sum()}")
print(f"  Bills without container: {bills['_container_clean'].isna().sum()}")

# Filter to valid containers only
bills_valid = bills[bills['_container_clean'].notna()].copy()

# Match to shipment universe (container OR SIPL)
bills_matched = bills_valid[
    (bills_valid['_container_clean'].isin(shipment_containers)) |
    (bills_valid['sipl_inv'].astype(str).str.strip().isin(shipment_sipls))
].copy()

print(f"  Bills with valid containers: {len(bills_valid)}")
print(f"  Bills matched to shipments: {len(bills_matched)}")
print(f"  Match rate: {100*len(bills_matched)/len(bills_valid):.1f}%")

# Mark pending
bills_matched['_is_pending'] = is_pending(bills_matched['bill_inv'])
print(f"  Pending bills (PEND marker): {bills_matched['_is_pending'].sum()}")

# ============================================================================
# STEP 4: LOAD AND CLASSIFY GL
# ============================================================================
print("\nSTEP 4: Load GL and classify by category")
print("-" * 80)

gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

gl_data = pd.concat([gl_1275, gl_1313], ignore_index=True)

print(f"  GL 1275: {len(gl_1275)}")
print(f"  GL 1313: {len(gl_1313)}")
print(f"  Combined: {len(gl_data)}")

# Classify GL by description
def classify_gl_category(description):
    if pd.isna(description):
        return 'OTHER'
    desc = str(description).upper()
    if 'OCEAN FREIGHT' in desc or ('FREIGHT' in desc and 'OCEAN' in desc):
        return 'Ocean Freight'
    elif 'CUSTOM' in desc:
        return 'Customs'
    elif 'DUTY' in desc:
        return 'Duty'
    elif 'DRAYAGE' in desc or 'CARTAGE' in desc or 'LOCAL' in desc:
        return 'Drayage'
    else:
        return 'Other'

gl_data['_category'] = gl_data['description'].apply(classify_gl_category)

category_dist = gl_data['_category'].value_counts()
print(f"  GL classification:")
for cat, count in category_dist.items():
    pct = 100 * count / len(gl_data)
    print(f"    {cat}: {count} ({pct:.1f}%)")

# ============================================================================
# STEP 5: MATCH GL TO BILLS
# ============================================================================
print("\nSTEP 5: Match GL to Bills via invoice number")
print("-" * 80)

# Normalize invoice numbers
if 'invoice' in gl_data.columns:
    gl_data['_invoice_num'] = gl_data['invoice'].astype(str).str.strip().str.upper()
else:
    gl_data['_invoice_num'] = None

bills_matched['_invoice_num'] = bills_matched['bill_inv'].astype(str).str.strip().str.upper()

# Create bill to container mapping
bill_to_container = dict(zip(bills_matched['_invoice_num'], bills_matched['_container_clean']))
bill_to_sipl = dict(zip(bills_matched['_invoice_num'], bills_matched['sipl_inv']))

# Map GL to bills
gl_data['_matched_container'] = gl_data['_invoice_num'].map(bill_to_container)
gl_data['_matched_sipl'] = gl_data['_invoice_num'].map(bill_to_sipl)

gl_matched = gl_data[gl_data['_matched_container'].notna()].copy()

print(f"  GL matched to bills: {len(gl_matched)}")
print(f"  Match rate: {100*len(gl_matched)/len(gl_data):.1f}%")

if len(gl_matched) > 0:
    category_matched = gl_matched['_category'].value_counts()
    print(f"  Matched GL by category:")
    for cat, count in category_matched.items():
        pct = 100 * count / len(gl_matched)
        print(f"    {cat}: {count} ({pct:.1f}%)")

# ============================================================================
# STEP 6: BUILD INVOICE COMPLIANCE MATRIX (PER-SIPL)
# ============================================================================
print("\nSTEP 6: Score invoice compliance per SIPL")
print("-" * 80)

required_categories = ['Ocean Freight', 'Customs', 'Duty', 'Drayage']
compliance_rows = []

for sipl in sorted(shipment_sipls):
    # Get shipment info
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
        gl_cat = gl_matched[gl_matched['_category'] == cat]

        # Match by container or SIPL
        gl_for_sipl = gl_cat[
            (gl_cat['_matched_container'] == container) |
            (gl_cat['_matched_sipl'] == sipl)
        ]

        if len(gl_for_sipl) > 0:
            compliance_row[f'{cat}_status'] = 'Complete'
            complete_cats.append(cat)
        else:
            # Check if pending
            bills_for_cat = bills_matched[
                (bills_matched['_container_clean'] == container) |
                (bills_matched['sipl_inv'] == sipl)
            ]
            if len(bills_for_cat) > 0 and bills_for_cat['_is_pending'].any():
                compliance_row[f'{cat}_status'] = 'Pending'
                pending_cats.append(cat)
            else:
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
    pct = 100 * count / len(invoice_compliance)
    print(f"    {status}: {count} ({pct:.1f}%)")

# ============================================================================
# FINAL SUMMARY
# ============================================================================
print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
Shipment Universe:
  Total international shipments: {len(shipment_master)}
  Distinct containers: {len(shipment_containers)}
  Distinct SIPLs: {len(shipment_sipls)}

Bills Processing:
  Total: {len(bills)}
  Valid containers: {len(bills_valid)}
  Matched to shipments: {len(bills_matched)}
  Pending markers: {bills_matched['_is_pending'].sum()}

GL Processing:
  Total: {len(gl_data)}
  Matched to bills: {len(gl_matched)}

Invoice Compliance:
  Complete: {(invoice_compliance['overall_status'] == 'Complete').sum()}
  Pending: {(invoice_compliance['overall_status'] == 'Pending').sum()}
  Missing: {(invoice_compliance['overall_status'] == 'Missing').sum()}
""")

# Save results
invoice_compliance.to_csv('invoice_compliance_correct.csv', index=False)
print(f"[OK] Saved to invoice_compliance_correct.csv")

EOF
