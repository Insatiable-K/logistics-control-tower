# -*- coding: utf-8 -*-
"""
Quick diagnostic: Check column names in dashboard_data.xlsx for spacing issues
"""

import pandas as pd
from pathlib import Path

print("=" * 80)
print("  EXCEL COLUMN DIAGNOSTIC")
print("=" * 80)

xlsx_file = Path("data/dashboard_data.xlsx")

if not xlsx_file.exists():
    print(f"\n❌ File not found: {xlsx_file}")
    print("Run: python build_dashboard_data.py")
    exit(1)

print(f"\n✓ Found: {xlsx_file}")

try:
    xls = pd.ExcelFile(xlsx_file)
    print(f"\nSheets available: {xls.sheet_names}")

    # Check each sheet for column name issues
    print("\n" + "=" * 80)
    print("COLUMN NAME ANALYSIS")
    print("=" * 80)

    for sheet_name in ["invoice_compliance", "shipment_mapping", "bills"]:
        if sheet_name not in xls.sheet_names:
            print(f"\n⚠️  Sheet '{sheet_name}' not found")
            continue

        print(f"\n--- {sheet_name} ---")
        df = pd.read_excel(xls, sheet_name)

        if df.empty:
            print(f"  ⚠️  Empty sheet")
            continue

        print(f"  Total rows: {len(df)}")
        print(f"  Total columns: {len(df.columns)}")
        print(f"\n  Column names (showing any spaces):")

        for i, col in enumerate(df.columns, 1):
            has_leading_space = col != col.lstrip()
            has_trailing_space = col != col.rstrip()
            spaces_str = ""
            if has_leading_space:
                spaces_str += " [LEADING SPACE]"
            if has_trailing_space:
                spaces_str += " [TRAILING SPACE]"

            print(f"    {i:2d}. |{col}|{spaces_str}")

        # Check for common problem columns
        print(f"\n  Looking for problem columns:")
        problem_cols = [c for c in df.columns if 'po' in c.lower() or 'number' in c.lower()]
        if problem_cols:
            print(f"    Found 'po' or 'number' columns:")
            for col in problem_cols:
                print(f"      - {col!r}")
        else:
            print(f"    None found")

except Exception as e:
    print(f"\n❌ Error: {type(e).__name__}: {e}")
    print("\nTry:")
    print("  1. Close the Excel file if it's open")
    print("  2. Run: python build_dashboard_data.py")
    exit(1)

print("\n" + "=" * 80)
print("RECOMMENDATIONS")
print("=" * 80)

print("""
If you see [TRAILING SPACE] or [LEADING SPACE]:
  - This causes KeyError when accessing columns
  - Solution: Already fixed in updated code
  - Run: python build_dashboard_data.py (again)

If you don't see invoice_compliance sheet:
  - ETL didn't complete successfully
  - Run: python build_dashboard_data.py (check for errors)

If columns look correct:
  - Delete old data/dashboard_data.xlsx
  - Run: python build_dashboard_data.py
  - Upload fresh file to app
""")

print("=" * 80)
