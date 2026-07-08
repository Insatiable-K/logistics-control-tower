# -*- coding: utf-8 -*-

import re
import pandas as pd
from logic import get_engine

# =============================================================================
# LOAD DATA
# =============================================================================

engine = get_engine()

freight_bills = pd.read_sql(
    "SELECT * FROM freight_bills",
    engine
)

gl_bills = pd.read_sql(
    "SELECT * FROM gl_bills",
    engine
)

bills = pd.read_sql(
    "SELECT * FROM bills",
    engine
)

print("FREIGHT BILLS :", freight_bills.shape)
print("GL BILLS      :", gl_bills.shape)
print("BILLS         :", bills.shape)

# =============================================================================
# BUILD MASTER
# =============================================================================

master = (
    bills[
        bills["sipl_inv"].notna()
        &
        bills["container"].notna()
    ][
        [
            "sipl_inv",
            "container"
        ]
    ]
    .drop_duplicates()
)

print("MASTER ROWS :", len(master))
print("UNIQUE SIPLS :", master["sipl_inv"].nunique())
print("UNIQUE CONTAINERS :", master["container"].nunique())

# =============================================================================
# LOOKUP SETS
# =============================================================================

sipl_set = set(
    master["sipl_inv"]
    .astype(str)
    .str.upper()
)

container_set = set(
    master["container"]
    .astype(str)
    .str.upper()
)

# =============================================================================
# EXTRACT FROM NOTES
# =============================================================================

def extract_sipl(note):

    if pd.isna(note):
        return None

    candidates = re.findall(
        r'(\d{5,6}[A-Z]?)',
        str(note).upper()
    )

    matches = [
        x for x in candidates
        if x in sipl_set
    ]

    return matches[0] if matches else None


def extract_container(note):

    if pd.isna(note):
        return None

    candidates = re.findall(
        r'([A-Z]{4}\d{7})',
        str(note).upper()
    )

    matches = [
        x for x in candidates
        if x in container_set
    ]

    return matches[0] if matches else None


bills["sipl_from_notes"] = (
    bills["notes"]
    .apply(extract_sipl)
)

bills["container_from_notes"] = (
    bills["notes"]
    .apply(extract_container)
)

# =============================================================================
# FINAL SIPL
# =============================================================================

bills["sipl_final"] = bills["sipl_inv"]

mask = (
    bills["sipl_final"].isna()
    &
    bills["sipl_from_notes"].notna()
)

bills.loc[
    mask,
    "sipl_final"
] = bills.loc[
    mask,
    "sipl_from_notes"
]

# =============================================================================
# CONTAINER FROM NOTES
# =============================================================================

bills["container_final"] = bills["container"]

mask = (
    bills["container_final"].isna()
    &
    bills["container_from_notes"].notna()
)

bills.loc[
    mask,
    "container_final"
] = bills.loc[
    mask,
    "container_from_notes"
]

# =============================================================================
# CONTAINER FROM SIPL MASTER
# =============================================================================

container_map = (
    master
    .drop_duplicates("sipl_inv")
    .set_index("sipl_inv")["container"]
)

bills["container_from_sipl"] = (
    bills["sipl_final"]
    .map(container_map)
)

mask = (
    bills["container_final"].isna()
    &
    bills["container_from_sipl"].notna()
)

bills.loc[
    mask,
    "container_final"
] = bills.loc[
    mask,
    "container_from_sipl"
]

# =============================================================================
# RESULTS
# =============================================================================

print("\nSIPL RESULTS")
print("Original :", bills["sipl_inv"].notna().sum())
print("Final    :", bills["sipl_final"].notna().sum())
print(
    "Recovered:",
    bills["sipl_final"].notna().sum()
    -
    bills["sipl_inv"].notna().sum()
)

print("\nCONTAINER RESULTS")
print("Original :", bills["container"].notna().sum())
print("Final    :", bills["container_final"].notna().sum())
print(
    "Recovered:",
    bills["container_final"].notna().sum()
    -
    bills["container"].notna().sum()
)

# =============================================================================
# CONTAINER-LINKED BILLS
# =============================================================================

container_bills = bills[
    bills["container_final"].notna()
].copy()

print("\nCONTAINER BILLS :", container_bills.shape)

print(
    "UNIQUE CONTAINERS :",
    container_bills["container_final"].nunique()
)

# =============================================================================
# CONTAINER BILLING PROFILE
# =============================================================================

container_profile = (
    container_bills
    .groupby("container")
    .agg(
        sipls=("sipl_inv", "nunique"),
        bills=("bill_inv", "nunique")
    )
    .reset_index()
)

container_profile["bills_per_sipl"] = (
    container_profile["bills"] /
    container_profile["sipls"]
).round(2)

container_profile["sipls_per_bill"] = (
    container_profile["sipls"] /
    container_profile["bills"]
).round(2)

# Classification
container_profile["status"] = "1:1"

container_profile.loc[
    container_profile["bills"] > container_profile["sipls"],
    "status"
] = "Multiple Bills"

container_profile.loc[
    container_profile["bills"] < container_profile["sipls"],
    "status"
] = "Missing Bills?"

print("=" * 80)
print("CONTAINER BILLING PROFILE")
print("=" * 80)

print(container_profile.head())

print("\nStatus Summary")
print(container_profile["status"].value_counts())

print("\nAverage Bills per Container :", round(container_profile["bills"].mean(),2))
print("Average SIPLs per Container :", round(container_profile["sipls"].mean(),2))
print("Average Bills per SIPL      :", round(container_profile["bills_per_sipl"].mean(),2))
# =============================================================================
# FINAL BILLS DATASET
# =============================================================================

container_bills = container_bills.drop(
    columns=[
        "supplier",
        "sipl_inv",
        "container",
        "notes",
        "sipl_from_notes",
        "container_from_notes",
        "container_from_sipl"
    ],
    errors="ignore"
)

container_bills = container_bills.rename(
    columns={
        "sipl_final": "sipl",
        "container_final": "container"
    }
)

print("\nFINAL BILLS COLUMNS")
for col in container_bills.columns:
    print(col)

# =============================================================================
# GL BILLS
# =============================================================================

gl_bills = gl_bills[
    gl_bills["type"] == "Bill"
].copy()

# =============================================================================
# CLEAN DESCRIPTION
# =============================================================================

gl_bills["description_clean"] = (
    gl_bills["description"]
    .str.upper()
    .str.strip()
)

# Remove PO references
po_mask = gl_bills["description_clean"].str.contains(
    r"PO",
    case=False,
    na=False
)

gl_bills.loc[
    po_mask,
    "description_clean"
] = pd.NA

# Standardize categories
gl_bills["description_clean"] = (
    gl_bills["description_clean"]
    .replace({
        "OCEAN FREIGHT": "OF",
        "AIR FREIGHT": "OF",
        "MIS": "MISC"
    })
)

# Drop blank descriptions
gl_bills = gl_bills[
    gl_bills["description_clean"].notna()
].copy()

print("GL BILLS :", gl_bills.shape)

print(
    gl_bills["description_clean"]
    .value_counts()
)

# =============================================================================
# STEP 1 - MATCH BILLS TO GL
# =============================================================================

matched_bills = container_bills.merge(
    gl_bills,
    left_on="bill_inv",
    right_on="invoice",
    how="left"
)

print("\nMATCHED TO GL")
print(matched_bills.shape)

# =============================================================================
# STEP 2 - LOAD SHIPMENT MAPPING
# =============================================================================

shipment_mapping = pd.read_sql(
    """
    SELECT
        container_id,
        po_number,
        sipl_number
    FROM shipment_mapping
    """,
    engine
)

shipment_mapping = shipment_mapping.rename(
    columns={
        "container_id": "container",
        "sipl_number": "sipl"
    }
)

shipment_mapping["container"] = (
    shipment_mapping["container"]
    .astype(str)
    .str.upper()
    .str.strip()
)

shipment_mapping["sipl"] = (
    shipment_mapping["sipl"]
    .astype(str)
    .str.upper()
    .str.strip()
)

shipment_mapping = shipment_mapping.drop_duplicates()

print("\nSHIPMENT MAPPING")
print(shipment_mapping.shape)

# =============================================================================
# STEP 3 - MAP PO TO EVERY BILL
# =============================================================================

matched_bills = matched_bills.merge(
    shipment_mapping,
    on=[
        "container",
        "sipl"
    ],
    how="left"
)

print("\nMATCHED BILLS")
print(matched_bills.shape)

# =============================================================================
# STEP 4 - NORMALIZE BILL TYPES
# =============================================================================

from rapidfuzz import process, fuzz

matched_bills["description_clean"] = (
    matched_bills["description_clean"]
    .fillna("")
    .astype(str)
    .str.upper()
    .str.strip()
)

VALID_TYPES = [
    "OF",
    "CUSTOMS",
    "DUTY",
    "DRAYAGE"
]

def normalize_bill_type(text):

    if text == "":
        return None

    # ----------------------------------------------------------
    # OCEAN FREIGHT
    # ----------------------------------------------------------

    if text == "OF":
        return "OF"

    if "OCEAN" in text:
        return "OF"

    if "AIR FREIGHT" in text:
        return "OF"

    if "AIRFREIGHT" in text:
        return "OF"

    # ----------------------------------------------------------
    # CUSTOMS
    # ----------------------------------------------------------

    if "CUSTOM" in text:
        return "CUSTOMS"

    # ----------------------------------------------------------
    # DUTY
    # ----------------------------------------------------------

    if "DUTY" in text:
        return "DUTY"

    # ----------------------------------------------------------
    # DRAYAGE
    # ----------------------------------------------------------

    if "DRAY" in text:
        return "DRAYAGE"

    # ----------------------------------------------------------
    # FUZZY MATCH (TYPO RECOVERY)
    # ----------------------------------------------------------

    match = process.extractOne(
        text,
        VALID_TYPES,
        scorer=fuzz.ratio
    )

    if match:

        value, score, _ = match

        if score >= 85:
            return value

    return None


matched_bills["bill_type"] = (
    matched_bills["description_clean"]
    .apply(normalize_bill_type)
)

# =============================================================================
# STEP 5 - QC
# =============================================================================

print("\n" + "=" * 80)
print("NORMALIZED BILL TYPES")
print("=" * 80)

print(
    matched_bills["bill_type"]
    .value_counts(dropna=False)
)

print("\nUNCLASSIFIED DESCRIPTIONS")
print("-" * 80)

print(
    matched_bills.loc[
        matched_bills["bill_type"].isna(),
        "description_clean"
    ]
    .value_counts()
)

# =============================================================================
# STEP 6 - KEEP ONLY COMPLIANCE BILLS
# =============================================================================

compliance_bills = matched_bills[
    matched_bills["bill_type"].notna()
].copy()

print("\n" + "=" * 80)
print("COMPLIANCE DATASET")
print("=" * 80)

print("Rows                :", len(compliance_bills))
print("Unique Containers   :", compliance_bills["container"].nunique())
print("Unique SIPLs        :", compliance_bills["sipl"].nunique())
print("Unique POs          :", compliance_bills["po_number"].nunique())
print("Unique Bills        :", compliance_bills["bill_inv"].nunique())

print("\nBILL TYPE BREAKDOWN")
print(
    compliance_bills["bill_type"]
    .value_counts()
)





























