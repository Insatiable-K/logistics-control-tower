# -*- coding: utf-8 -*-
"""
Diagnostic script to investigate why 99.9% of bills aren't matching to shipments.
"""

import pandas as pd
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_container, clean_text,
)

SOURCE_DIR = Path(__file__).parent
FILES = {
    "in_transit": SOURCE_DIR / "In-Transit List by SIPL.xls",
    "bills": SOURCE_DIR / "Bills.xls",
}

print("=" * 80)
print("  BILL MATCHING DIAGNOSTIC")
print("=" * 80)

# Load data
in_transit = standardize_columns(load_html_table(FILES["in_transit"]))
in_transit["container"] = in_transit["container"].apply(clean_container)
in_transit["sipl"] = clean_text(in_transit["sipl"])

bills = standardize_columns(load_html_table(FILES["bills"]))
print(f"Bills columns: {bills.columns.tolist()}")

bills["container"] = bills["container"].apply(clean_container) if "container" in bills.columns else None
bills["sipl_inv"] = bills["sipl_inv"] if "sipl_inv" in bills.columns else (bills["sipl"] if "sipl" in bills.columns else None)
if bills["sipl_inv"] is not None:
    bills["sipl"] = clean_text(bills["sipl_inv"])
else:
    bills["sipl"] = None

bills["bill_inv"] = clean_text(bills.get("bill_inv")).str.upper() if "bill_inv" in bills.columns else None

print(f"\nIn-Transit data:")
print(f"  Total rows: {len(in_transit)}")
print(f"  Unique containers: {in_transit['container'].nunique()}")
print(f"  Unique SIPLs: {in_transit['sipl'].nunique()}")
print(f"  Containers with null: {in_transit['container'].isna().sum()}")
print(f"  SIPLs with null: {in_transit['sipl'].isna().sum()}")

print(f"\nBills data:")
print(f"  Total rows: {len(bills)}")
print(f"  Unique containers: {bills['container'].nunique()}")
print(f"  Unique SIPLs: {bills['sipl'].nunique()}")
print(f"  Containers with null: {bills['container'].isna().sum()}")
print(f"  SIPLs with null: {bills['sipl'].isna().sum()}")

print(f"\nShipment Universe (unique Container-SIPL pairs):")
shipment_universe = in_transit[["container", "sipl"]].drop_duplicates()
print(f"  {len(shipment_universe)} unique pairs")

print(f"\nSample In-Transit data (first 10):")
print(in_transit[["container", "sipl"]].head(10).to_string())

print(f"\nSample Bills data (first 10):")
print(bills[["container", "sipl", "bill_inv"]].head(10).to_string())

# Diagnostic: Check for matching issues
print(f"\n" + "=" * 80)
print("  DIAGNOSTIC ANALYSIS")
print("=" * 80)

# Check if any bills SIPL values exist in in_transit
bills_sipl_in_transit = bills[bills["sipl"].isin(in_transit["sipl"])].shape[0]
print(f"\nBills with SIPL in In-Transit: {bills_sipl_in_transit:,}")

# Check if any bills container values exist in in_transit
bills_container_in_transit = bills[bills["container"].isin(in_transit["container"])].shape[0]
print(f"Bills with Container in In-Transit: {bills_container_in_transit:,}")

# Check for SIPL value range
print(f"\nSIPL value analysis:")
print(f"  In-Transit SIPL sample: {in_transit['sipl'].dropna().head(5).tolist()}")
print(f"  Bills SIPL sample: {bills['sipl'].dropna().head(5).tolist()}")

print(f"\nContainer value analysis:")
print(f"  In-Transit Container sample: {in_transit['container'].dropna().head(5).tolist()}")
print(f"  Bills Container sample: {bills['container'].dropna().head(5).tolist()}")

print("\n" + "=" * 80)
