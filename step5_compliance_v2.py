#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
STEP 5: Complete Invoice Compliance Pipeline (Corrected)
Match: Bills -> Containers, GL -> Bills -> Containers
"""

import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_container,
    build_container_lookup, resolve_container, is_pending
)

print("=" * 80)
print("STEP 5: REBUILD INVOICE COMPLIANCE (Pipeline v2 - GL via Bills)")
print("=" * 80)

# Load all sources
in_transit = standardize_columns(load_html_table(Path("In-Transit List by SIPL.xls")))
inventory = standardize_columns(load_html_table(Path("Inventory In Transit - Detail .xls")))
bills = standardize_columns(load_html_table(Path("Bills.xls")))
gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

# Clean containers
for df in [in_transit, inventory, bills]:
    if 'container' in df.columns:
        df['_container_clean'] = df['container'].apply(clean_container)

print("\nPhase 1: Build Global Lookup")
print("-" * 80)

lookup = build_container_lookup({
    "in_transit": in_transit,
    "inventory": inventory,
    "bills": bills,
})

print(f"  SIPL->Container mappings: {len(lookup['sipl_to_container'])}")

print("\nPhase 2: Create Shipment Master")
print("-" * 80)

in_transit['_resolved_container'] = in_transit.apply(lambda row: resolve_container(row, lookup), axis=1)
shipment_master = in_transit[in_transit['status'] == 'INTRANSIT'].copy()
shipment_master = shipment_master[shipment_master['_resolved_container'].notna()].copy()

print(f"  Active INTRANSIT shipments: {len(shipment_master)}")
print(f"  Distinct containers: {shipment_master['_resolved_container'].nunique()}")

active_containers = set(shipment_master['_resolved_container'].unique())
active_sipls = set(shipment_master['sipl'].unique())

print("\nPhase 3: Filter Bills to Active Universe")
print("-" * 80)

bills['_resolved_container'] = bills.apply(lambda row: resolve_container(row, lookup), axis=1)
bills_matched = bills[bills['_resolved_container'].isin(active_containers)].copy()

print(f"  Total bills: {len(bills)}")
print(f"  Bills matching active containers: {len(bills_matched)}")

# Clean bill amounts for GL matching
bills_matched['invoice_num'] = bills_matched['bill_inv'].astype(str).str.strip().str.upper()

# Mark pending
bills_matched['is_pending'] = is_pending(bills_matched['bill_inv'])
print(f"  Pending bills (PEND marker): {bills_matched['is_pending'].sum()}")

print("\nPhase 4: Load and Prepare GL")
print("-" * 80)

gl_data = pd.concat([gl_1275, gl_1313], ignore_index=True)
print(f"  Total GL entries: {len(gl_data)}")

# Extract invoice number from GL
if 'invoice' in gl_data.columns:
    gl_data['invoice_num'] = gl_data['invoice'].astype(str).str.strip().str.upper()
    print(f"  GL entries with invoice: {gl_data['invoice_num'].notna().sum()}")
else:
    gl_data['invoice_num'] = None

# Classify GL by description
def classify_gl_category(description):
    if pd.isna(description):
        return 'OTHER'
    desc = str(description).upper()
    if 'OCEAN' in desc or 'FREIGHT' in desc or 'OCEAN FREIGHT' in desc:
        return 'OF'
    elif 'CUSTOM' in desc:
        return 'CUSTOMS'
    elif 'DUTY' in desc:
        return 'DUTY'
    elif 'DRAYAGE' in desc or 'CARTAGE' in desc or 'LOCAL' in desc:
        return 'DRAYAGE'
    elif 'ACCESSORIAL' in desc or 'ACCESSORY' in desc:
        return 'ACCESSORIAL'
    else:
        return 'OTHER'

if 'description' in gl_data.columns:
    gl_data['category'] = gl_data['description'].apply(classify_gl_category)
else:
    gl_data['category'] = 'OTHER'

category_dist = gl_data['category'].value_counts()
print(f"  GL classification:")
for cat, count in category_dist.items():
    pct = 100 * count / len(gl_data)
    print(f"    {cat}: {count} ({pct:.1f}%)")

print("\nPhase 5: Match GL to Bills")
print("-" * 80)

# Create bill-to-container lookup
bill_to_container = dict(zip(bills_matched['invoice_num'], bills_matched['_resolved_container']))

# Add container to GL via bill invoice
gl_data['_resolved_container'] = gl_data['invoice_num'].map(bill_to_container)

gl_matched = gl_data[gl_data['_resolved_container'].notna()].copy()
print(f"  GL matched to bills: {len(gl_matched)}")

if len(gl_matched) > 0:
    category_matched = gl_matched['category'].value_counts()
    print(f"  Matched GL by category:")
    for cat, count in category_matched.items():
        pct = 100 * count / len(gl_matched)
        print(f"    {cat}: {count} ({pct:.1f}%)")

print("\n" + "=" * 80)
print("PHASE 6: BUILD INVOICE COMPLIANCE MATRIX")
print("=" * 80)

compliance_rows = []

for sipl in sorted(active_sipls):
    shipment = shipment_master[shipment_master['sipl'] == sipl].iloc[0]
    container = shipment['_resolved_container']

    # Get bills and GL for this container
    bills_for_container = bills_matched[bills_matched['_resolved_container'] == container]
    gl_for_container = gl_matched[gl_matched['_resolved_container'] == container]

    categories = ['OF', 'CUSTOMS', 'DUTY', 'DRAYAGE']

    compliance_row = {
        'sipl': sipl,
        'container': container,
        'supplier': shipment.get('supplier', ''),
        'status': shipment.get('status', ''),
    }

    # Score each category
    missing_categories = []
    pending_categories = []
    complete_categories = []

    for cat in categories:
        gl_cat = gl_for_container[gl_for_container['category'] == cat]

        if len(gl_cat) > 0:
            compliance_row[f'{cat}_status'] = 'Complete'
            complete_categories.append(cat)
        elif len(bills_for_container) > 0 and bills_for_container['is_pending'].any():
            compliance_row[f'{cat}_status'] = 'Pending'
            pending_categories.append(cat)
        else:
            compliance_row[f'{cat}_status'] = 'Missing'
            missing_categories.append(cat)

    # Build missing/pending category lists
    compliance_row['missing_categories'] = ', '.join(missing_categories) if missing_categories else None
    compliance_row['pending_categories'] = ', '.join(pending_categories) if pending_categories else None

    # Overall status
    if len(missing_categories) > 0:
        compliance_row['overall_status'] = 'Missing'
    elif len(pending_categories) > 0:
        compliance_row['overall_status'] = 'Pending'
    else:
        compliance_row['overall_status'] = 'Complete'

    compliance_rows.append(compliance_row)

invoice_compliance = pd.DataFrame(compliance_rows)

print(f"\nInvoice Compliance Matrix:")
print(f"  Total SIPLs: {len(invoice_compliance)}")

status_dist = invoice_compliance['overall_status'].value_counts()
for status in ['Complete', 'Pending', 'Missing']:
    count = status_dist.get(status, 0)
    pct = 100 * count / len(invoice_compliance)
    print(f"    {status}: {count} ({pct:.1f}%)")

print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

print(f"""
Active Shipment Universe:
  SIPLs: {len(shipment_master)}
  Containers: {shipment_master['_resolved_container'].nunique()}

Bills Processing:
  Total: {len(bills)}
  Matched to active containers: {len(bills_matched)} ({100*len(bills_matched)/len(bills):.1f}%)
  Removed (historical): {len(bills) - len(bills_matched)} ({100*(len(bills) - len(bills_matched))/len(bills):.1f}%)

GL Processing:
  Total: {len(gl_data)}
  Matched to bills: {len(gl_matched)} ({100*len(gl_matched)/len(gl_data):.1f}%)

Invoice Compliance:
  Complete: {(invoice_compliance['overall_status'] == 'Complete').sum()}
  Pending: {(invoice_compliance['overall_status'] == 'Pending').sum()}
  Missing: {(invoice_compliance['overall_status'] == 'Missing').sum()}
""")

# Save results
invoice_compliance.to_csv('invoice_compliance_results.csv', index=False)
print("[OK] Saved results to invoice_compliance_results.csv")
