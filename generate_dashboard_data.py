import pandas as pd
from logic import get_engine



# ==================================================
# RUN ETL FIRST
# ==================================================

import subprocess
import sys

print("=" * 60)
print("STEP 1: RUNNING ETL")
print("=" * 60)

try:

    subprocess.run(
        [sys.executable, "ETL_Clean_Load.py"],
        check=True
    )

    print("\nETL completed successfully")

except subprocess.CalledProcessError:

    print("\nETL FAILED")
    raise SystemExit(1)

print("=" * 60)
print("STEP 2: EXPORTING DASHBOARD DATA")
print("=" * 60)
# ==================================================
# CONNECT
# ==================================================

print("Connecting to SQL Server...")

engine = get_engine()

# ==================================================
# TABLES TO EXPORT
# ==================================================

TABLES = [
    "bookings",
    "shipment_mapping",
    "open_po",
    "in_transit",
    "inventory_intransit"
]

# ==================================================
# EXPORT
# ==================================================

output_file = "dashboard_data.xlsx"

print("Reading SQL tables...")

with pd.ExcelWriter(
    output_file,
    engine="openpyxl"
) as writer:

    for table in TABLES:

        try:

            print(f"Exporting {table}")

            df = pd.read_sql(
                f"SELECT * FROM {table}",
                engine
            )

            df.to_excel(
                writer,
                sheet_name=table[:31],   # Excel limit
                index=False
            )

            print(
                f"  Rows: {len(df):,}"
            )

        except Exception as e:

            print(
                f"FAILED: {table}"
            )

            print(e)

print("=" * 60)
print("Dashboard Package Created")
print(output_file)
print("=" * 60)