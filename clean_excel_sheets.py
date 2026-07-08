#!/usr/bin/env python
"""
clean_excel_sheets.py
---------------------
Iterate over every Excel workbook in the repository, apply a set of generic
clean‑up rules, and write a cleaned copy.

*   Column names → stripped, lower‑cased, spaces → underscores.
*   Drop rows/columns that are entirely NaN.
*   Auto‑convert any column whose name contains a date‑hint to datetime.
*   Save cleaned files under ./cleaned/ preserving the original filename.
    A backup of the original file is also saved as <name>_bak.<ext>.

Adjust `CLEANING_RULES` or add custom logic as needed.
"""

import os
import pathlib
import shutil
import sys

import pandas as pd

# ---------------------------------------------------------------------------
# 1️⃣  Configuration
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent  # repository root (where the script lives)
OUT_DIR = ROOT / "cleaned"
OUT_DIR.mkdir(exist_ok=True)

# File extensions we will process
EXCEL_GLOBS = ["**/*.xls", "**/*.xlsx"]

# Heuristic: treat a column as a date if its header contains one of these tokens
DATE_TOKENS = {"date", "dt", "eta", "day", "month", "year", "time"}

# ---------------------------------------------------------------------------
# 2️⃣  Helper utilities
# ---------------------------------------------------------------------------
def is_date_column(col_name: str) -> bool:
    """Return True if a column name appears to hold dates."""
    lowered = col_name.lower()
    return any(tok in lowered for tok in DATE_TOKENS)


def clean_container_series(series: pd.Series) -> pd.Series:
    """Clean container‑ID columns to keep only valid ISO‑6346 codes.
    Steps:
    * Upper‑case
    * Strip whitespace
    * Remove any non‑alphanumeric characters
    * Keep only strings matching the pattern 4 letters + 7 digits (e.g. ABCD1234567)
    Invalid entries become NaN.
    """
    # Ensure string type
    s = series.astype(str).str.upper().str.replace(r'[^A-Z0-9]', '', regex=True)
    pattern = r'^[A-Z]{4}\d{7}$'
    # Keep only valid matches; others become NaN
    return s.where(s.str.fullmatch(pattern))

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the generic cleaning pipeline to a DataFrame."""
    # ① Normalise column names
    df = df.rename(columns=lambda s: s.strip().lower().replace(" ", "_"))

    # ② Drop completely empty rows / columns
    df = df.dropna(axis=0, how="all")  # rows
    df = df.dropna(axis=1, how="all")  # columns

    # ③ Clean container‑ID columns (any column name containing "container")
    for col in df.columns:
        if "container" in col:
            original_invalid = df[col].notna().sum()
            df[col] = clean_container_series(df[col])
            # Log how many entries became NaN after cleaning (optional)
            cleaned_invalid = df[col].isna().sum()
            if cleaned_invalid > 0:
                print(f"   ↳ Cleaned container column '{col}': {cleaned_invalid} invalid entries set to NaN")

    # ④ Coerce date‑like columns
    for col in df.columns:
        if is_date_column(col):
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # ⑤ (Optional) Fill missing values – leave as NaN for now
    # df = df.fillna("")   # Uncomment if you prefer empty strings

    return df


def process_file(src_path: pathlib.Path):
    """Read, clean, and write a single Excel workbook."""
    print(f"🗂️  Processing {src_path}")

    # Preserve a backup of the original file (in case the user wants to revert)
    backup_path = src_path.with_name(src_path.stem + "_bak" + src_path.suffix)
    shutil.copy2(src_path, backup_path)

    # Determine appropriate engine for older .xls files
    engine = None
    if src_path.suffix.lower() == ".xls":
        engine = "xlrd"  # legacy .xls format

    # Load workbook – we read every sheet into a dict of DataFrames
    try:
        xl = pd.read_excel(src_path, sheet_name=None, engine=engine)
    except Exception as exc:
        print(f"❌  Could not read {src_path.name}: {exc}")
        return

    cleaned_sheets = {}
    for sheet_name, df in xl.items():
        cleaned = clean_dataframe(df)
        cleaned_sheets[sheet_name] = cleaned

    # Destination path (same filename, under ./cleaned)
    dest_path = OUT_DIR / src_path.name

    # Write cleaned workbook – preserve original sheet names
    try:
        with pd.ExcelWriter(dest_path, engine="openpyxl") as writer:
            for sheet_name, cleaned_df in cleaned_sheets.items():
                cleaned_df.to_excel(writer, sheet_name=sheet_name, index=False)
        print(f"✅  Cleaned file written to {dest_path}")
    except Exception as exc:
        print(f"❌  Failed to write cleaned file for {src_path.name}: {exc}")


# ---------------------------------------------------------------------------
# 3️⃣  Main driver
# ---------------------------------------------------------------------------
def main():
    # Walk the repository and collect matching files
    excel_files = []
    for pattern in EXCEL_GLOBS:
        excel_files.extend(ROOT.glob(pattern))

    if not excel_files:
        print("🔎  No Excel files found – nothing to do.")
        sys.exit(0)

    print(f"🔎  Found {len(excel_files)} Excel workbook(s) to clean:")
    for f in excel_files:
        print(f"   • {f.relative_to(ROOT)}")

    for file_path in excel_files:
        process_file(file_path)


if __name__ == "__main__":
    main()
