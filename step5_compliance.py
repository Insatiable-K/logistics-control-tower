#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
STEP 5: Complete Invoice Compliance Pipeline
Rebuild compliance matrix with new data
"""

import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_container, clean_date,
    build_container_lookup, resolve_container, is_pending
)

print("=" * 80)
print("STEP 5: REBUILD INVOICE COMPLIANCE (Complete Pipeline)")
print("=" * 80)

# Load all sources
in_transit = standardize_columns(load_html_table(Path("In-Transit List by SIPL.xls")))
inventory = standardize_columns(load_html_table(Path("Inventory In Transit - Detail .xls")))
bills = standardize_columns(load_html_table(Path("Bills.xls")))
gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

# Clean containers
for df in [in_transit, inventory, bills, gl_1275, gl_1313]:
    if 'container' in df.columns:
        df['_container_clean'] = df['container'].apply(clean_container)
    else:
        df['_container_clean'] = pd.NA

print("\nPhase 1: Build Global Lookup")
print("-" * 80)

lookup = build_container_lookup({
    "in_transit": in_transit,
    "inventory": inventory,
    "bills": bills,
})

print(f"  SIPL->Container mappings: {len(lookup['sipl_to_container'])}")
print(f"  PO->Container mappings: {len(lookup['po_to_container'])}")

print("\nPhase 2: Create Shipment Master")
print("-" * 80)

# Resolve containers in IN_TRANSIT
in_transit['_resolved_container'] = in_transit.apply(lambda row: resolve_container(row, lookup), axis=1)
in_transit['container_id'] = in_transit['_resolved_container']

# Filter to INTRANSIT only
shipment_master = in_transit[in_transit['status'] == 'INTRANSIT'].copy()
shipment_master = shipment_master[shipment_master['container_id'].notna()].copy()

print(f"  Total IN_TRANSIT: {len(in_transit)}")
print(f"  With INTRANSIT status: {(in_transit['status'] == 'INTRANSIT').sum()}")
print(f"  With containers: {len(shipment_master)}")
print(f"  Distinct containers: {shipment_master['container_id'].nunique()}")
print(f"  Distinct SIPLs: {shipment_master['sipl'].nunique()}")

# Get active universe
active_containers = set(shipment_master['container_id'].unique())
active_sipls = set(shipment_master['sipl'].unique())

print("\nPhase 3: Filter Bills to Active Universe")
print("-" * 80)

bills['_resolved_container'] = bills.apply(lambda row: resolve_container(row, lookup), axis=1)

# Match containers
bills_matched_container = bills[bills['_resolved_container'].isin(active_containers)].copy()
print(f"  Total bills: {len(bills)}")
print(f"  Bills with resolved containers: {(bills['_resolved_container'].notna()).sum()}")
print(f"  Bills matching active containers: {len(bills_matched_container)}")

# Mark pending
if 'bill_inv' in bills_matched_container.columns:
    bills_matched_container['is_pending'] = is_pending(bills_matched_container['bill_inv'])
else:
    bills_matched_container['is_pending'] = False

print(f"  Pending invoices (PEND marker): {bills_matched_container['is_pending'].sum()}")

print("\nPhase 4: Classify GL Accounts")
print("-" * 80)

# Combine GL accounts
gl_data = pd.concat([gl_1275, gl_1313], ignore_index=True)

# Clean GL data
if 'date' in gl_data.columns:
    gl_data['_date'] = clean_date(gl_data['date'])
else:
    gl_data['_date'] = pd.NA

if 'container' in gl_data.columns:
    gl_data['_container_clean'] = gl_data['container'].apply(clean_container)
else:
    gl_data['_container_clean'] = pd.NA

print(f"  GL 1275 rows: {len(gl_1275)}")
print(f"  GL 1313 rows: {len(gl_1313)}")
print(f"  Combined GL rows: {len(gl_data)}")

# Match GL to bills
gl_data['_resolved_container'] = gl_data.apply(lambda row: resolve_container(row, lookup), axis=1)
gl_matched = gl_data[gl_data['_resolved_container'].isin(active_containers)].copy()

print(f"  GL rows matching active containers: {len(gl_matched)}")

# Classify by GL description
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

if 'description' in gl_matched.columns:
    gl_matched['category'] = gl_matched['description'].apply(classify_gl_category)
else:
    gl_matched['category'] = 'OTHER'

category_dist = gl_matched['category'].value_counts()
print(f"  GL classification:")
for cat, count in category_dist.items():
    pct = 100 * count / len(gl_matched)
    print(f"    {cat}: {count} ({pct:.1f}%)")

print("\n" + "=" * 80)
print("PHASE 5: BUILD INVOICE COMPLIANCE MATRIX")
print("=" * 80)

# Build compliance per SIPL
compliance_rows = []

for sipl in sorted(active_sipls):
    shipment = shipment_master[shipment_master['sipl'] == sipl].iloc[0]
    container = shipment['container_id']

    # Get bills and GL for this shipment
    bills_for_sipl = bills_matched_container[bills_matched_container['_resolved_container'] == container]
    gl_for_sipl = gl_matched[gl_matched['_resolved_container'] == container]

    # Invoice categories to track
    categories = ['OF', 'CUSTOMS', 'DUTY', 'DRAYAGE']

    compliance_row = {
        'sipl': sipl,
        'container': container,
        'supplier': shipment.get('supplier', ''),
        'status': shipment.get('status', ''),
        'port_eta': shipment.get('port_eta', ''),
        'vessel': shipment.get('vessel', ''),
    }

    # Score each category
    for cat in categories:
        gl_cat = gl_for_sipl[gl_for_sipl['category'] == cat]
        bills_cat = bills_for_sipl

        if len(gl_cat) > 0:
            compliance_row[f'{cat}_status'] = 'Complete'
        elif len(bills_cat) > 0 and bills_cat['is_pending'].any():
            compliance_row[f'{cat}_status'] = 'Pending'
        else:
            compliance_row[f'{cat}_status'] = 'Missing'

    # Overall status
    statuses = [compliance_row.get(f'{cat}_status', 'Missing') for cat in categories]
    if all(s == 'Complete' for s in statuses):
        compliance_row['overall_status'] = 'Complete'
    elif any(s == 'Pending' for s in statuses):
        compliance_row['overall_status'] = 'Pending'
    else:
        compliance_row['overall_status'] = 'Missing'

    compliance_rows.append(compliance_row)

invoice_compliance = pd.DataFrame(compliance_rows)

print(f"\nInvoice Compliance Matrix:")
print(f"  Total SIPLs: {len(invoice_compliance)}")
print(f"  Overall status distribution:")
for status in ['Complete', 'Pending', 'Missing']:
    count = (invoice_compliance['overall_status'] == status).sum()
    pct = 100 * count / len(invoice_compliance)
    print(f"    {status}: {count} ({pct:.1f}%)")

print("\n" + "=" * 80)
print("SUMMARY: FINAL RESULTS")
print("=" * 80)

print(f"""
Container Resolution Results:
  Active shipments (INTRANSIT): {len(shipment_master)}
  Distinct containers: {shipment_master['container_id'].nunique()}
  Bills matching active universe: {len(bills_matched_container)}
  GL entries classified: {len(gl_matched)}

Invoice Compliance Scoring:
  SIPLs with Complete invoices: {(invoice_compliance['overall_status'] == 'Complete').sum()}
  SIPLs with Pending invoices: {(invoice_compliance['overall_status'] == 'Pending').sum()}
  SIPLs with Missing invoices: {(invoice_compliance['overall_status'] == 'Missing').sum()}

Data Quality:
  Bills removed (historical/inactive): {len(bills) - len(bills_matched_container)} ({100*(len(bills) - len(bills_matched_container))/len(bills):.1f}%)
  Bills kept (operational): {len(bills_matched_container)} ({100*len(bills_matched_container)/len(bills):.1f}%)
""")
