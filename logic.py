# =============================================================================
# LOGIC LAYER — LOGISTICS CONTROL TOWER
# =============================================================================

import urllib
import pandas as pd
import pyodbc
from sqlalchemy import create_engine, text


# -----------------------------------------------------------------------------
# CONNECTION
# -----------------------------------------------------------------------------
def get_engine():
    drivers = pyodbc.drivers()
    driver = "ODBC Driver 17 for SQL Server"
    if driver not in drivers:
        driver = "ODBC Driver 18 for SQL Server"

    conn_str = (
        f"DRIVER={{{driver}}};"
        "SERVER=localhost\\SQLEXPRESS01;"
        "DATABASE=logistics_db;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )

    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn_str)}",
        pool_pre_ping=True
    )

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return engine


# -----------------------------------------------------------------------------
# LOAD SHIPMENT MAPPING
# -----------------------------------------------------------------------------
def load_shipment_mapping(engine):

    df = pd.read_sql("SELECT * FROM shipment_mapping", engine)
    
    df["container_id"] = df["container_id"].astype(str).str.strip().str.upper()
    df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")
    df["sipl_number"] = df["sipl_number"].astype(str).str.strip()
    
    return df


# -----------------------------------------------------------------------------
# LOAD BOOKINGS (ENRICHED)
# -----------------------------------------------------------------------------
def load_bookings(engine):

    df = pd.read_sql("SELECT * FROM bookings", engine)

    # Normalize
    df["event_status"] = (
        df["event_status"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["event_date"] = pd.to_datetime(df["event_date"])
    df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")

    # Load mapping
    mapping = load_shipment_mapping(engine)

    # Merge mapping — only use container_id from mapping where bookings has none
    # FIX: use _map suffix to avoid ambiguity, then coalesce
    df = df.merge(
        mapping[["po_number", "container_id"]].drop_duplicates(),
        on="po_number",
        how="left",
        suffixes=("", "_map")
    )

    # Fill missing containers only
    if "container_id_map" in df.columns:
        df["container_id"] = df["container_id"].combine_first(df["container_id_map"])
        df = df.drop(columns=["container_id_map"])

    return df


# -----------------------------------------------------------------------------
# ROLLOVER — TRUE EVENT COUNT
# -----------------------------------------------------------------------------
def get_rollover_summary(bookings):

    df = bookings.copy()

    df = df[df["event_status"].str.contains("ROLL", na=False)]

    df = df.drop_duplicates(
        subset=["container_id", "event_date", "vessel", "etd"]
    )

    rollover_summary = (
        df.groupby("container_id")
        .size()
        .reset_index(name="rollover_count")
    )

    return rollover_summary


# =============================================================================
# ====================== EXECUTION + CONTROL TOWER LAYER =======================
# =============================================================================
# Purpose:
# Build execution dataset (intransit + mapping)
# Add time-based KPIs (NO status dependency for core logic)
# Add exception detection
# Add ETA delay (promise vs current ETA)
# =============================================================================


# -----------------------------------------------------------------------------
# BUILD EXECUTION MASTER (SIPL + INTRANSIT + MAPPING)
# -----------------------------------------------------------------------------
def build_execution_master(engine):

    df_eta = pd.read_sql("SELECT * FROM in_transit", engine)
    df_inv = pd.read_sql("SELECT * FROM inventory_intransit", engine)
    df_map = pd.read_sql("SELECT * FROM shipment_mapping", engine)

    # Normalize column names
    df_eta.columns = df_eta.columns.str.lower().str.strip()
    df_inv.columns = df_inv.columns.str.lower().str.strip()
    df_map.columns = df_map.columns.str.lower().str.strip()

    # -------------------------------------------------------------------------
    # Rename everything BEFORE merging so no _x/_y suffixes are ever created.
    # Each source column gets a unique name → outer merge is clean.
    # -------------------------------------------------------------------------
    rename_eta = {
        "container":      "container_eta",
        "supplier":       "supplier_eta",
        "fr_forwarder":   "forwarder_eta",
        "departure_port": "departure_port_eta",
        "ship_to_location": "final_destination",   # already unique
        "location_eta":   "delivery_eta",          # already unique
    }
    rename_inv = {
        "container":          "container_inv",
        "supplier":           "supplier_inv",
        "freight_forwarder":  "forwarder_inv",
        "departure_port":     "departure_port_inv",
        "ship_to":            "destination_inv",
        "eta_date":           "delivery_eta_inv",
        "arrival_port":       "arrival_port",
    }

    df_eta = df_eta.rename(columns={k: v for k, v in rename_eta.items() if k in df_eta.columns})
    df_inv = df_inv.rename(columns={k: v for k, v in rename_inv.items() if k in df_inv.columns})

    # Keep only columns we actually use
    eta_keep = ["sipl", "supplier_eta", "container_eta",
                "forwarder_eta", "departure_port_eta",
                "final_destination", "port_eta", "delivery_eta",
                "lfd", "sipl_status"]
    inv_keep = ["sipl", "supplier_inv", "container_inv",
                "forwarder_inv", "departure_port_inv",
                "destination_inv", "delivery_eta_inv",
                "arrival_port", "ship_b_l_date"]

    df_eta = df_eta[[c for c in eta_keep if c in df_eta.columns]]
    df_inv = df_inv[[c for c in inv_keep if c in df_inv.columns]]

    # Outer merge — no column overlap except "sipl", so no _x/_y suffixes
    df = pd.merge(df_eta, df_inv, on="sipl", how="outer")

    # Parse port_eta before sorting
    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce")
    df = df.sort_values("port_eta").drop_duplicates(subset=["sipl"], keep="last")

    # -------------------------------------------------------------------------
    # Coalesce paired columns — eta source is preferred, inv is fallback
    # -------------------------------------------------------------------------
    def _coalesce(df, primary, fallback):
        """Return primary column, filling NaN from fallback if it exists."""
        if primary in df.columns and fallback in df.columns:
            return df[primary].fillna(df[fallback])
        elif primary in df.columns:
            return df[primary]
        elif fallback in df.columns:
            return df[fallback]
        else:
            return pd.Series(pd.NA, index=df.index, dtype=object)

    df["container_id"]    = _coalesce(df, "container_eta",      "container_inv")
    df["supplier"]        = _coalesce(df, "supplier_eta",       "supplier_inv")
    df["forwarder"]       = _coalesce(df, "forwarder_eta",      "forwarder_inv")
    df["departure_port"]  = _coalesce(df, "departure_port_eta", "departure_port_inv")
    df["final_destination"] = _coalesce(df, "final_destination", "destination_inv")
    df["delivery_eta"]    = _coalesce(df, "delivery_eta",       "delivery_eta_inv")

    # Drop raw split columns — keep only coalesced ones
    drop_cols = ["container_eta", "container_inv",
                 "supplier_eta",  "supplier_inv",
                 "forwarder_eta", "forwarder_inv",
                 "departure_port_eta", "departure_port_inv",
                 "destination_inv", "delivery_eta_inv"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    # -------------------------------------------------------------------------
    # Merge shipment mapping (sipl → container_id + po_number)
    # container_id already set from execution sources — mapping fills gaps
    # -------------------------------------------------------------------------
    map_cols = [c for c in ["sipl_number", "container_id", "po_number"] if c in df_map.columns]
    df_map = df_map[map_cols].rename(columns={"sipl_number": "sipl"})

    # Rename mapping container_id to avoid clash
    df_map = df_map.rename(columns={"container_id": "container_id_map"})

    df = df.merge(df_map, on="sipl", how="left")

    # Fill container_id from mapping only where still missing
    if "container_id_map" in df.columns:
        df["container_id"] = df["container_id"].fillna(df["container_id_map"])
        df = df.drop(columns=["container_id_map"])

    # -------------------------------------------------------------------------
    # Final datetime parsing
        # -------------------------------------------------------------------------
    df["port_eta"]      = pd.to_datetime(df["port_eta"], errors="coerce")
    df["delivery_eta"]  = pd.to_datetime(df["delivery_eta"], errors="coerce")
    df["lfd"]           = pd.to_datetime(df["lfd"], errors="coerce")
    
    if "ship_b_l_date" in df.columns:
        df["ship_b_l_date"] = pd.to_datetime(df["ship_b_l_date"], errors="coerce")

    return df

###############################################################################
# BUILD INVOICE COMPLIANCE
###############################################################################

def build_invoice_compliance(engine):

    import re
    from rapidfuzz import process, fuzz

    # -------------------------------------------------------------------------
    # LOAD TABLES
    # -------------------------------------------------------------------------

    bills = pd.read_sql(
        "SELECT * FROM bills",
        engine
    )

    gl_bills = pd.read_sql(
        "SELECT * FROM gl_bills",
        engine
    )

    shipment_mapping = load_shipment_mapping(engine)

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
    # STEP 4A - FLAG PENDING BILLS
    # =============================================================================
    
    matched_bills["is_pending"] = (
        matched_bills["bill_inv"]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.strip()
        .eq("PENDING")
    )
    
    print("\nPending Bills")
    print(
        matched_bills["is_pending"]
        .value_counts()
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

    # -------------------------------------------------------------------------
    # BUILD COMPLIANCE MATRIX
    # -------------------------------------------------------------------------
    
    required = [
        "OF",
        "CUSTOMS",
        "DUTY",
        "DRAYAGE"
    ]
    
    # One record per Container + PO + Bill Type
    invoice_matrix = (
        compliance_bills
        .groupby(
            [
                "container",
                "po_number",
                "bill_type"
            ]
        )
        .agg(
            bill_exists=(
                "is_pending",
                lambda x: (~x).any()
            ),
            pending_exists=(
                "is_pending",
                "any"
            )
        )
        .reset_index()
    )
    
    # Pivot Actual Bills
    actual = (
        invoice_matrix
        .pivot_table(
            index=[
                "container",
                "po_number"
            ],
            columns="bill_type",
            values="bill_exists",
            fill_value=False
        )
    )
    
    actual.columns = [
        f"{c}_ACTUAL"
        for c in actual.columns
    ]
    
    # Pivot Pending Bills
    pending = (
        invoice_matrix
        .pivot_table(
            index=[
                "container",
                "po_number"
            ],
            columns="bill_type",
            values="pending_exists",
            fill_value=False
        )
    )
    
    pending.columns = [
        f"{c}_PENDING"
        for c in pending.columns
    ]
    
    # Combine
    invoice_matrix = (
        pd.concat(
            [
                actual,
                pending
            ],
            axis=1
        )
        .reset_index()
    )
    
    # Ensure every bill exists
    for bill in required:
    
        if f"{bill}_ACTUAL" not in invoice_matrix.columns:
            invoice_matrix[f"{bill}_ACTUAL"] = False
    
        if f"{bill}_PENDING" not in invoice_matrix.columns:
            invoice_matrix[f"{bill}_PENDING"] = False
    
    
    # ------------------------------------------------------------
    # ------------------------------------------------------------
    # MISSING BILL LOGIC
    # ------------------------------------------------------------
    
    def get_missing(row):
    
        missing = []
    
        for bill in required:
    
            actual = row[f"{bill}_ACTUAL"]
            pending = row[f"{bill}_PENDING"]
    
            # Missing only if neither an actual bill
            # nor a pending placeholder exists
            if (not actual) and (not pending):
                missing.append(bill)
    
        return ", ".join(missing)
    
    
    # ------------------------------------------------------------
    # PENDING BILL LOGIC
    # ------------------------------------------------------------
    
    def get_pending(row):
    
        pending_list = []
    
        for bill in required:
    
            actual = row[f"{bill}_ACTUAL"]
            pending = row[f"{bill}_PENDING"]
    
            # Show only bills that are still pending
            if pending and not actual:
                pending_list.append(bill)
    
        return ", ".join(pending_list)
    
    
    # ------------------------------------------------------------
    # FINAL FLAGS
    # ------------------------------------------------------------
    
    invoice_matrix["missing_bills"] = (
        invoice_matrix.apply(
            get_missing,
            axis=1
        )
    )
    
    invoice_matrix["pending_bills"] = (
        invoice_matrix.apply(
            get_pending,
            axis=1
        )
    )
    
    invoice_matrix["invoice_ready"] = (
        invoice_matrix["missing_bills"] == ""
    )
        
    return invoice_matrix
###############################################################################
# ARRIVING CONTAINERS WITH MISSING INVOICES
###############################################################################

def get_arriving_invoice_risk(df_exec, invoice_compliance, days=3):
    today = pd.Timestamp.today().normalize()
    
    arriving = df_exec[
        (df_exec["port_eta"] >= today) &
        (df_exec["port_eta"] <= today + pd.Timedelta(days=days))
    ].copy()
    
    arriving = arriving.merge(
        invoice_compliance,
        left_on=["container_id", "po_number"],
        right_on=["container", "po_number"],
        how="left"
    )
    
    # FIX: If a container has NO bills at all, populate all required fields as missing
    all_required_bills = ", ".join(["OF", "CUSTOMS", "DUTY", "DRAYAGE"])
    arriving["missing_bills"] = arriving["missing_bills"].fillna(all_required_bills)
    arriving["invoice_ready"] = arriving["invoice_ready"].fillna(False)
    
    risk = arriving[arriving["invoice_ready"] != True].copy()
    
    return risk

# =============================================================================
# =============================== KPI LAYER ====================================
# =============================================================================

    # -----------------------------------------------------------------------------
    # CONTAINERS ON WATER (TIME-BASED)
    # -----------------------------------------------------------------------------
def get_containers_on_water(df):
    today = pd.Timestamp.today().normalize()
    return df[df["port_eta"] >= today]["container_id"].nunique()


    # -----------------------------------------------------------------------------
    # ARRIVING TODAY
    # -----------------------------------------------------------------------------
def get_arriving_today(df):
    today = pd.Timestamp.today().normalize()
    return df[df["port_eta"].dt.normalize() == today]["container_id"].nunique()


    # -----------------------------------------------------------------------------
    # NEXT 7 DAYS ARRIVALS
    # -----------------------------------------------------------------------------
def get_next_7_days_arrivals(df):
    today = pd.Timestamp.today().normalize()

    return df[
        (df["port_eta"] >= today) &
        (df["port_eta"] <= today + pd.Timedelta(days=7))
    ]["container_id"].nunique()


    # -----------------------------------------------------------------------------
    # LOCATION ETA REACHED
    # -----------------------------------------------------------------------------
def get_location_reached(df):
    today = pd.Timestamp.today().normalize()
    return df[
        df["delivery_eta"].notna() & (df["delivery_eta"] <= today)
    ]["container_id"].nunique()

    # -----------------------------------------------------------------------------
    # LOCATION ETA — NEXT 7 DAYS
    # -----------------------------------------------------------------------------
def get_location_next_7_days(df):

    today = pd.Timestamp.today().normalize()

    return df[
        (df["delivery_eta"].notna()) &
        (df["delivery_eta"] >= today) &
        (df["delivery_eta"] <= today + pd.Timedelta(days=7))
    ]["container_id"].nunique()


# -----------------------------------------------------------------------------
# PORT ETA NEXT 7 DAYS — DOCUMENT STAGE RISK
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# PORT ETA RISK — PAST + NEXT 7 DAYS (WITH RISK TYPE)
# -----------------------------------------------------------------------------
def get_port_eta_doc_risk(df):

    df = df.copy()

    today = pd.Timestamp.today().normalize()

    # -----------------------------
    # CLEAN STATUS
    # -----------------------------
    df["sipl_status_clean"] = (
        df["sipl_status"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    # -----------------------------
    # DEFINE DOCUMENT RISK STATUS
    # -----------------------------
    doc_stage_status = [
        "SIPL READY",
        "DOCUMENTS SENT",
        "NEED DOCUMENTS"
    ]

    # -----------------------------
    # FILTER: PAST + NEXT 7 DAYS
    # -----------------------------
    risk_df = df[
        (df["port_eta"].notna()) &
        (
            (df["port_eta"] <= today) |   # already arrived
            (
                (df["port_eta"] >= today) &
                (df["port_eta"] <= today + pd.Timedelta(days=7))
            )
        ) &
        (df["sipl_status_clean"].isin(doc_stage_status))
    ].copy()

    # -----------------------------
    # ADD RISK TYPE
    # -----------------------------
    risk_df["risk_type"] = "Upcoming"

    risk_df.loc[
        risk_df["port_eta"] < today,
        "risk_type"
    ] = "Already at Port"

    return risk_df


# -----------------------------------------------------------------------------
# PROMISED VS ACTUAL ETA ANALYSIS (ENHANCED)
# -----------------------------------------------------------------------------
def get_eta_performance(df_exec, bookings):

    import pandas as pd

    # -----------------------------
    # STEP 1 — LATEST BOOKING PER CONTAINER
    # -----------------------------
    bookings_clean = bookings.copy()
    bookings_clean = bookings_clean.sort_values("event_date")

    latest_booking = (
        bookings_clean.groupby("container_id", as_index=False)
        .last()
    )

    latest_booking = latest_booking.rename(columns={
        "eta": "promised_eta",
        "fr_forwarder": "forwarder_booking",
        "etd": "booked_etd"
    })

    # -----------------------------
    # STEP 2 — MERGE EXECUTION + BOOKING
    # -----------------------------
    df = df_exec.merge(
        latest_booking[
            [c for c in [
                "container_id",
                "booked_etd",
                "promised_eta",
                "forwarder_booking"
            ] if c in latest_booking.columns]
        ],
        on="container_id",
        how="left"
    )

    # -----------------------------
    # STEP 3 — DATE CLEANUP
    # -----------------------------
    date_cols = ["booked_etd", "promised_eta", "port_eta", "ship_b_l_date"]

    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # -----------------------------
    # STEP 4 — CALCULATIONS
    # -----------------------------

    # Booked Transit
    df["booked_transit"] = (df["promised_eta"] - df["booked_etd"]).dt.days

    # Actual Transit (based on BL, not ETD)
    df["actual_transit"] = (df["port_eta"] - df["ship_b_l_date"]).dt.days

    # Shipping Delay (ETD → BL)
    df["shipping_delay"] = (df["ship_b_l_date"] - df["booked_etd"]).dt.days

    # Transit Delay (actual vs planned)
    df["transit_delay"] = df["actual_transit"] - df["booked_transit"]
    
    if "ship_b_l_date" not in df.columns:
        raise ValueError("ship_b_l_date missing from execution layer — fix build_execution_master()")

    return df

# =============================================================================
# CONTAINER DATA VALIDATION (DESTINATION + PORT + DATA COMPLETENESS)
# =============================================================================
def get_container_data_issues(df_exec):

    import pandas as pd

    df = df_exec.copy()

    # -----------------------------
    # STANDARDIZE CONTAINER COLUMN
    # -----------------------------
    if "container_id" not in df.columns and "container" in df.columns:
        df = df.rename(columns={"container": "container_id"})

    if "container_id" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    # -----------------------------
    # KEEP VALID CONTAINERS
    # -----------------------------
    df = df[df["container_id"].notna()].copy()

    # -----------------------------
    # DEFINE CRITICAL FIELDS (ONLY IF PRESENT)
    # -----------------------------
    possible_fields = [
        "final_destination",
        "departure_port",
        "arrival_port",
        "port_eta",
        "ship_b_l_date",
        "supplier",
        "freight_forwarder"
    ]

    critical_fields = [c for c in possible_fields if c in df.columns]

    # -----------------------------
    # NORMALIZE TEXT FIELDS
    # -----------------------------
    for col in ["final_destination", "departure_port", "arrival_port"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .replace({"": pd.NA, "nan": pd.NA, "none": pd.NA})
            )

    # -----------------------------
    # SAFE UNIQUE COUNT
    # -----------------------------
    def safe_nunique(series):
        return series.dropna().nunique()

    # -----------------------------
    # AGGREGATE PER CONTAINER
    # -----------------------------
    agg_dict = {}

    if "final_destination" in df.columns:
        agg_dict["destination_count"] = ("final_destination", safe_nunique)

    if "departure_port" in df.columns:
        agg_dict["departure_port_count"] = ("departure_port", safe_nunique)

    if "arrival_port" in df.columns:
        agg_dict["arrival_port_count"] = ("arrival_port", safe_nunique)

    container_check = (
        df.groupby("container_id")
        .agg(**agg_dict)
        .reset_index()
    )

    # Fill missing count columns safely
    for col in ["destination_count", "departure_port_count", "arrival_port_count"]:
        if col not in container_check.columns:
            container_check[col] = 0

    # -----------------------------
    # MISSING FIELD DETECTION (ONLY REAL FIELDS)
    # -----------------------------
    def get_missing_fields(group):
        missing = []
        for col in critical_fields:
            if group[col].dropna().empty:
                missing.append(col)
        return missing

    missing_map = (
        df.groupby("container_id")
        .apply(get_missing_fields)
        .reset_index(name="missing_fields")
    )

    container_check = container_check.merge(
        missing_map,
        on="container_id",
        how="left"
    )

    # -----------------------------
    # CLASSIFY ISSUES
    # -----------------------------
    def classify_issue(row):
        issues = []

        if row["destination_count"] > 1:
            issues.append("Multiple Destinations")

        if row["departure_port_count"] > 1:
            issues.append("Multiple Departure Ports")

        if row["arrival_port_count"] > 1:
            issues.append("Multiple Arrival Ports")

        if row["missing_fields"]:
            issues.append("Missing: " + ", ".join(row["missing_fields"]))

        return " | ".join(issues)

    container_check["issue_type"] = container_check.apply(classify_issue, axis=1)

    # -----------------------------
    # SEVERITY (CORRECTED)
    # -----------------------------
    def classify_severity(row):

        missing = row["missing_fields"]

        # CRITICAL → only if fields actually exist AND are missing
        if (
            "final_destination" in missing or
            ("departure_port" in missing and "arrival_port" in missing)
        ):
            return "CRITICAL"

        # HIGH → structural or partial missing
        if (
            row["destination_count"] > 1 or
            row["departure_port_count"] > 1 or
            row["arrival_port_count"] > 1 or
            "departure_port" in missing or
            "arrival_port" in missing
        ):
            return "HIGH"

        if missing:
            return "MEDIUM"

        return ""

    container_check["severity"] = container_check.apply(classify_severity, axis=1)

    # -----------------------------
    # FILTER PROBLEM CONTAINERS
    # -----------------------------
    problem_containers = container_check[
        container_check["issue_type"] != ""
    ]["container_id"]

    # -----------------------------
    # ROW LEVEL OUTPUT
    # -----------------------------
    issues_df = df[df["container_id"].isin(problem_containers)].copy()

    issues_df = issues_df.merge(
        container_check[["container_id", "issue_type", "severity"]],
        on="container_id",
        how="left"
    )

    # -----------------------------
    # SORT BY SEVERITY
    # -----------------------------
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "": 3}

    issues_df["severity_rank"] = issues_df["severity"].map(severity_order)

    issues_df = issues_df.sort_values(
        by=["severity_rank", "container_id"],
        ascending=[True, True]
    ).drop(columns=["severity_rank"])

    return issues_df, container_check

# -----------------------------------------------------------------------------
# LFD RISK — BREACH + APPROACHING
# -----------------------------------------------------------------------------
def get_lfd_risk(df_exec, days_ahead=3):

    import pandas as pd

    df = df_exec.copy()

    today = pd.Timestamp.today().normalize()

    # -----------------------------
    # DATE CLEANUP
    # -----------------------------
    for col in ["lfd", "delivery_eta"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # -----------------------------
    # 🔴 BREACH (ALREADY LATE)
    # -----------------------------
    breach_df = df[
        (df["lfd"].notna()) &
        (df["delivery_eta"].notna()) &
        (df["delivery_eta"] > df["lfd"])
    ].copy()

    # -----------------------------
    # 🟡 APPROACHING RISK
    # -----------------------------
    approaching_df = df[
        (df["lfd"].notna()) &
        (df["delivery_eta"].notna()) &
        (df["lfd"] >= today) &
        (df["lfd"] <= today + pd.Timedelta(days=days_ahead)) &
        (df["delivery_eta"] >= df["lfd"])
    ].copy()

    return breach_df, approaching_df