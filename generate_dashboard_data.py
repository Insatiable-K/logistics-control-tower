import pandas as pd
from logic import get_engine

from datetime import datetime
from pathlib import Path

today = datetime.now().strftime("%Y-%m-%d")

output_folder = Path(
    r"C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\Dashboards\Container Movement Control Tower"
)

output_folder.mkdir(
    parents=True,
    exist_ok=True
)

dated_file = output_folder / f"dashboard_data_{today}.xlsx"
latest_file = output_folder / "dashboard_data.xlsx"

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


print("Reading SQL tables...")

# Load each table once
tables_data = {}

for table in TABLES:

    try:

        print(f"Loading {table}")

        tables_data[table] = pd.read_sql(
            f"SELECT * FROM {table}",
            engine
        )

        print(
            f"  Rows: {len(tables_data[table]):,}"
        )

    except Exception as e:

        print(f"FAILED: {table}")
        print(e)

# Create both files
for file_name in [dated_file, latest_file]:

    print(f"\nCreating {file_name}")

    with pd.ExcelWriter(
        file_name,
        engine="openpyxl"
    ) as writer:

        for table, df in tables_data.items():

            df.to_excel(
                writer,
                sheet_name=table[:31],
                index=False
            )

print("=" * 60)
print("Dashboard Packages Created")
print(f"Archive: {dated_file}")
print(f"Latest : {latest_file}")
print("=" * 60)