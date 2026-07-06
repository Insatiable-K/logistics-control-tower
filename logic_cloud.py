
import pandas as pd
# -----------------------------------------------------------------------------
# LOAD SHIPMENT MAPPING
# -----------------------------------------------------------------------------
def load_shipment_mapping(shipment_mapping):

    df = shipment_mapping.copy()

    df["container_id"] = (
        df["container_id"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["po_number"] = pd.to_numeric(
        df["po_number"],
        errors="coerce"
    ).astype("Int64")

    df["sipl_number"] = (
        df["sipl_number"]
        .astype(str)
        .str.strip()
    )

    return df


# -----------------------------------------------------------------------------
# LOAD BOOKINGS (ENRICHED)
# -----------------------------------------------------------------------------
def load_bookings(bookings_df, shipment_mapping_df):

    # Instead of SQL read
    df = bookings_df.copy()

    # Normalize
    df["event_status"] = (
        df["event_status"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["event_date"] = pd.to_datetime(
        df["event_date"],
        errors="coerce"
    )

    df["po_number"] = pd.to_numeric(
        df["po_number"],
        errors="coerce"
    ).astype("Int64")

    # Instead of loading from SQL
    mapping = load_shipment_mapping(
        shipment_mapping_df
    )

    # Merge mapping — only use container_id from mapping where bookings has none
    df = df.merge(
        mapping[["po_number", "container_id"]].drop_duplicates(),
        on="po_number",
        how="left",
        suffixes=("", "_map")
    )

    # Fill missing containers only
    if "container_id_map" in df.columns:
        df["container_id"] = (
            df["container_id"]
            .combine_first(df["container_id_map"])
        )

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


# -----------------------------------------------------------------------------
# FINAL EVENT DATE
# -----------------------------------------------------------------------------
def get_container_final_date(bookings):

    df = bookings.copy()

    final_dates = (
        df.sort_values("event_date")
        .groupby("container_id")["event_date"]
        .last()
        .reset_index(name="final_event_date")
    )

    return final_dates


# -----------------------------------------------------------------------------
# KPI — TOTAL BOOKED CONTAINERS
# -----------------------------------------------------------------------------
def get_total_booked_containers(bookings):

    df = bookings.copy()

    df = df[df["container_id"].notna()]
    df = df[df["etd"].notna()]

    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# KPI — CONTAINERS PLANNED TO SAIL
# -----------------------------------------------------------------------------
def get_containers_planned_to_sail(df_master, selected_date):

    df = df_master.copy()

    df["latest_etd"] = pd.to_datetime(df["latest_etd"])
    selected_date = pd.to_datetime(selected_date).normalize()

    sailed = df[
        df["latest_etd"].dt.normalize() == selected_date
    ]

    return sailed["container_id"].nunique()


# -----------------------------------------------------------------------------
# OPEN PO KPI
# -----------------------------------------------------------------------------
def get_open_po_kpi(open_po_df):

    df = open_po_df.copy()

    df.columns = [c.lower().strip() for c in df.columns]

    df["po_status"] = df["po_status"].astype(str).str.strip().str.upper()
    df["category"] = df["category"].astype(str).str.strip().str.upper()

    TARGET_STATUS = "INSPECTION REPORT APPROVED"

    TARGET_CATEGORIES = [
        "PQ SAMPLES",
        "PENTAL QUARTZ",
        "NATURAL STONE"
    ]

    df_filtered = df[
        (df["po_status"] == TARGET_STATUS) &
        (df["category"].isin(TARGET_CATEGORIES))
    ]

    total_pos = df_filtered["po"].nunique()

    category_breakdown = (
        df_filtered["category"]
        .value_counts()
        .reset_index()
    )

    category_breakdown.columns = ["category", "count"]

    return total_pos, category_breakdown


# -----------------------------------------------------------------------------
# CONTAINER MASTER
# -----------------------------------------------------------------------------
def build_container_master(bookings):

    df = bookings[bookings["container_id"].notna()].copy()

    start_etd = df.groupby("container_id")["etd"].min()

    latest_etd = (
        df.sort_values("event_date")
        .groupby("container_id")["etd"]
        .last()
    )

    promised_eta = (
        df.sort_values("event_date")
        .groupby("container_id")["eta"]
        .last()
    )

    po_count = df.groupby("container_id")["po_number"].nunique()

    container_master = pd.DataFrame({
        "start_etd": start_etd,
        "latest_etd": latest_etd,
        "promised_eta": promised_eta,
        "po_count": po_count
    }).reset_index()

    # Rollover
    rollover_summary = get_rollover_summary(bookings)
    container_master = container_master.merge(
        rollover_summary,
        on="container_id",
        how="left"
    )

    # Final event date
    final_dates = get_container_final_date(bookings)
    container_master = container_master.merge(
        final_dates,
        on="container_id",
        how="left"
    )

    container_master["rollover_count"] = container_master["rollover_count"].fillna(0)

    return container_master


# -----------------------------------------------------------------------------
# KPI — CONTAINERS BY DESTINATION (BOOKED ONLY, NOT INSPECTION APPROVED)
# FIX: Duplicate definition removed — keeping the corrected version only
# -----------------------------------------------------------------------------
def get_containers_by_destination(
    bookings,
    open_po_df
):

    open_po = open_po_df.copy()

    open_po.columns = [c.lower().strip() for c in open_po.columns]

    # Normalize
    open_po["po"] = pd.to_numeric(open_po["po"], errors="coerce").astype("Int64")
    open_po["po_status"] = open_po["po_status"].astype(str).str.strip().str.upper()
    open_po["ship_to_location"] = open_po["ship_to_location"].astype(str).str.strip()

    # Only booked containers (execution layer)
    booked = bookings[
        (bookings["container_id"].notna()) &
        (bookings["etd"].notna())
    ][["container_id", "po_number"]].drop_duplicates()

    # Join with open PO (to get destination)
    df = booked.merge(
        open_po[["po", "ship_to_location", "po_status"]],
        left_on="po_number",
        right_on="po",
        how="left"
    )

    # Exclude inspection approved (not booked yet)
    df = df[df["po_status"] != "INSPECTION REPORT APPROVED"]

    # Aggregate
    summary = (
        df.drop_duplicates(subset=["container_id", "ship_to_location"])
        .groupby("ship_to_location")["container_id"]
        .nunique()
        .reset_index(name="container_count")
        .sort_values("container_count", ascending=False)
    )

    return summary


# -----------------------------------------------------------------------------
# KPI — CONTAINERS SCHEDULED TO SAIL (MONTH)
# -----------------------------------------------------------------------------
def get_containers_sailed_by_month(df_master, selected_month):

    df = df_master.copy()

    df["latest_etd"] = pd.to_datetime(df["latest_etd"])

    df["month"] = df["latest_etd"].dt.to_period("M")

    selected_month = pd.Period(selected_month)

    return df[df["month"] == selected_month]["container_id"].nunique()


# =============================================================================
# ====================== NEW KPI LAYER — DATE RANGE LOGIC ======================
# =============================================================================


# -----------------------------------------------------------------------------
# 1. FIRST BOOKING DATE (TRUE BOOKING MOMENT)
# -----------------------------------------------------------------------------
def get_container_first_booking(bookings):

    df = bookings.copy()

    df = df[df["container_id"].notna()]
    df = df[df["etd"].notna()]

    first_booking = (
        df.sort_values("event_date")
        .groupby("container_id")["event_date"]
        .min()
        .reset_index(name="first_booking_date")
    )

    return first_booking


# -----------------------------------------------------------------------------
# 2. CONTAINERS BOOKED IN DATE RANGE (FLOW METRIC)
# -----------------------------------------------------------------------------
def get_containers_booked_in_range(bookings, start_date, end_date):

    first_booking = get_container_first_booking(bookings)

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    df = first_booking[
        (first_booking["first_booking_date"] >= start_date) &
        (first_booking["first_booking_date"] <= end_date)
    ]

    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# 3. ACTIVE CONTAINERS IN DATE RANGE (ACTIVITY METRIC)
# -----------------------------------------------------------------------------
def get_active_containers(bookings, start_date, end_date):

    df = bookings.copy()

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    df = df[
        (df["event_date"] >= start_date) &
        (df["event_date"] <= end_date)
    ]

    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# 4. CONTAINERS SCHEDULED TO SAIL IN DATE RANGE (PLANNED FLOW)
# -----------------------------------------------------------------------------
def get_containers_sailed_in_range(df_master, start_date, end_date):

    df = df_master.copy()

    df["latest_etd"] = pd.to_datetime(df["latest_etd"])

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    df = df[
        (df["latest_etd"] >= start_date) &
        (df["latest_etd"] <= end_date)
    ]

    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# PIPELINE DESTINATION (UNBOOKED)
# -----------------------------------------------------------------------------
def get_pipeline_destination(open_po_df):

    df = open_po_df.copy()

    df.columns = [c.lower().strip() for c in df.columns]

    df["po_status"] = df["po_status"].astype(str).str.strip().str.upper()
    df["ship_to_location"] = df["ship_to_location"].astype(str).str.strip()

    TARGET_STATUS = "INSPECTION REPORT APPROVED"

    df = df[df["po_status"] == TARGET_STATUS]

    summary = (
        df.groupby("ship_to_location")["po"]
        .nunique()
        .reset_index(name="po_count")
        .sort_values("po_count", ascending=False)
    )

    return summary


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
def build_execution_master(
    in_transit_df,
    inventory_intransit_df,
    shipment_mapping_df
):

    df_eta = in_transit_df.copy()

    df_inv = inventory_intransit_df.copy()

    df_map = shipment_mapping_df.copy()

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


# =============================================================================
# =============================== KPI LAYER ====================================
# =============================================================================

# -----------------------------------------------------------------------------
# TOTAL CONTAINERS
# -----------------------------------------------------------------------------
def get_total_containers(df):
    return df["container_id"].nunique()


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
# CURRENT WEEK ARRIVALS
# FIX: use .dt.isocalendar().week safely — also handle NaT rows
# -----------------------------------------------------------------------------
def get_current_week_arrivals(df):
    today = pd.Timestamp.today()
    current_week = today.isocalendar()[1]
    current_year = today.year

    mask = df["port_eta"].notna()
    iso = df.loc[mask, "port_eta"].dt.isocalendar()

    matched = df.loc[mask].loc[
        (iso["week"] == current_week) & (iso["year"] == current_year)
    ]

    return matched["container_id"].nunique()


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
# 7 DAY ARRIVAL EXCEPTION (NOT READY)
# -----------------------------------------------------------------------------
def get_7day_not_ready(df):

    today = pd.Timestamp.today().normalize()

    # FIX: guard against missing sipl_status column
    if "sipl_status" not in df.columns:
        return 0

    ready_status = [
        "Scheduled for Delivery",
        "Delivery Pending",
        "At branch",
        "D.Os  Received"
    ]

    return df[
        (df["port_eta"] >= today) &
        (df["port_eta"] <= today + pd.Timedelta(days=7)) &
        (~df["sipl_status"].isin(ready_status))
    ]["container_id"].nunique()


# -----------------------------------------------------------------------------
# NEED INVOICE
# FIX: case-insensitive check for "INVOICE" in sipl_status
# -----------------------------------------------------------------------------
def get_invoice_needed(df):
    if "sipl_status" not in df.columns:
        return 0
    return df[
        df["sipl_status"].str.upper().str.contains("INVOICE", na=False)
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
# DELIVERY DONE BUT STATUS NOT UPDATED
# -----------------------------------------------------------------------------
def get_status_lag(df):

    today = pd.Timestamp.today().normalize()

    if "sipl_status" not in df.columns:
        return 0

    return df[
        (df["delivery_eta"].notna()) &
        (df["delivery_eta"] < today) &
        (~df["sipl_status"].isin([
            "At branch",
            "Scheduled for Delivery"
        ]))
    ]["container_id"].nunique()


# -----------------------------------------------------------------------------
# LFD BREACH (BUSINESS LOGIC)
# FIX: both columns must be non-null for comparison to be meaningful
# -----------------------------------------------------------------------------
def get_lfd_breach(df):
    mask = df["lfd"].notna() & df["delivery_eta"].notna()
    return df[mask & (df["lfd"] < df["delivery_eta"])]["container_id"].nunique()


# -----------------------------------------------------------------------------
# PROMISE VS ETA DELAY
# FIX: po_number may be int/str — align types before merge
# -----------------------------------------------------------------------------
def get_eta_delay(
    open_po_df,
    df
):

    booking = open_po_df[
        ["po", "eta_port"]
    ].copy()
    booking.columns = booking.columns.str.lower()

    booking.rename(columns={"eta_port": "promised_eta"}, inplace=True)

    booking["promised_eta"] = pd.to_datetime(
        booking["promised_eta"], errors="coerce"
    )

    # FIX: align po_number types
    booking["po"] = pd.to_numeric(booking["po"], errors="coerce").astype("Int64")

    df = df.copy()
    df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")

    df = df.merge(
        booking,
        left_on="po_number",
        right_on="po",
        how="left"
    )

    df["eta_delay"] = (
        df["port_eta"] - df["promised_eta"]
    ).dt.days

    return df

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

# ================================
# FUNCTION: FIRST PO RECEIVED DATE
# ================================
def get_pos_received_in_range(bookings):

    df = bookings.copy()

    # Keep only valid PO rows
    df = df[df["po_number"].notna()]

    # Get first occurrence of each PO
    first_seen = (
        df.sort_values("event_date")
        .groupby("po_number")["event_date"]
        .min()
        .reset_index(name="first_received_date")
    )

    return first_seen

# ==========================================
# KPI: BOOKINGS APPROVED (PO - DAILY EVENTS)
# ==========================================
def get_pos_approved_in_range(bookings, start_date, end_date):

    df = bookings.copy()

    # CLEAN
    df = df[df["po_number"].notna()].copy()
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")

    df["event_status"] = (
        df["event_status"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    # FILTER DATE RANGE
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    df = df[
        (df["event_date"] >= start_date) &
        (df["event_date"] <= end_date)
    ]

    # NON-HOLD = APPROVED EVENTS
    approved = df[df["event_status"] != "HOLD"]

    # COUNT UNIQUE PO FOR THAT DAY
    return approved["po_number"].nunique()

# ==========================================
# KPI: PO TO CONTAINER RATIO
# ==========================================
def get_po_to_container_ratio(bookings, start_date, end_date):

    # Approved POs (event-based)
    approved_pos = get_pos_approved_in_range(bookings, start_date, end_date)

    # Containers created (lifecycle)
    df = bookings.copy()

    df = df[df["container_id"].notna()].copy()
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")

    df["event_status"] = (
        df["event_status"].astype(str).str.upper().str.strip()
    )

    is_booking = (
        (df["event_status"] == "BOOKED") |
        ((df["event_status"] == "ROLLOVER") & df["container_id"].notna())
    )

    first_container = (
        df[is_booking]
        .groupby("container_id", as_index=False)["event_date"]
        .min()
    )

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    container_in_range = first_container[
        (first_container["event_date"] >= start_date) &
        (first_container["event_date"] <= end_date)
    ]

    total_containers = container_in_range["container_id"].nunique()

    ratio = 0
    if approved_pos > 0:
        ratio = round(total_containers / approved_pos, 2)

    return approved_pos, total_containers, ratio


# ==========================================
# KPI: POs WITHOUT CONTAINER
# ==========================================
def get_pos_without_container(bookings):

    df = bookings.copy()

    df = df[df["po_number"].notna()].copy()
    df["event_status"] = df["event_status"].astype(str).str.upper().str.strip()

    # Approved events
    approved = df[df["event_status"] != "HOLD"]

    # Get latest record per PO
    latest = (
        approved.sort_values("event_date")
        .groupby("po_number", as_index=False)
        .last()
    )

    # Missing container
    no_container = latest[latest["container_id"].isna()]

    return no_container["po_number"].nunique(), no_container

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


# -----------------------------------------------------------------------------
# ARRIVING / OUTSTANDING CONTAINERS WITH INVOICE ISSUES
#
# Returns ONE dataframe with ALL containers that have port_eta set and at least
# one bill issue (missing OR pending).  Callers filter by:
#   arrival_status == "Approaching"  → next `days` days
#   arrival_status == "Past ETA"     → already passed but bills still outstanding
#   missing_bills  != ""             → Missing Bills tab
#   pending_bills  != ""             → Pending Bills tab
# -----------------------------------------------------------------------------
def get_arriving_invoice_risk(df_exec, invoice_compliance, days=3):

    REQUIRED = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]
    ALL_MISSING = ", ".join(REQUIRED)

    today = pd.Timestamp.today().normalize()

    # ------------------------------------------------------------------
    # 1. ALL containers that have a port_eta (arriving or past)
    # ------------------------------------------------------------------
    df = df_exec[df_exec["port_eta"].notna()].copy()
    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce")

    # Arrival status label
    df["arrival_status"] = "Approaching"
    df.loc[df["port_eta"].dt.normalize() == today, "arrival_status"] = "Today"
    df.loc[df["port_eta"] < today,                 "arrival_status"] = "Past ETA"

    # ------------------------------------------------------------------
    # 2. Merge compliance matrix (container + po_number)
    # ------------------------------------------------------------------
    if not invoice_compliance.empty:
        df = df.merge(
            invoice_compliance,
            left_on=["container_id", "po_number"],
            right_on=["container", "po_number"],
            how="left"
        )
    else:
        # No compliance data at all — every container is missing everything
        for bill in REQUIRED:
            df[f"{bill}_ACTUAL"]  = False
            df[f"{bill}_PENDING"] = False
        df["missing_bills"] = ALL_MISSING
        df["pending_bills"] = ""
        df["invoice_ready"] = False

    # ------------------------------------------------------------------
    # 3. Containers with NO bill record at all → missing all 4
    # ------------------------------------------------------------------
    no_data_mask = df["invoice_ready"].isna()
    df.loc[no_data_mask, "missing_bills"] = ALL_MISSING
    df.loc[no_data_mask, "pending_bills"] = ""
    df.loc[no_data_mask, "invoice_ready"] = False
    for bill in REQUIRED:
        if f"{bill}_ACTUAL" in df.columns:
            df.loc[no_data_mask, f"{bill}_ACTUAL"]  = False
            df.loc[no_data_mask, f"{bill}_PENDING"] = False

    # ------------------------------------------------------------------
    # 4. Return ALL containers with any bill issue (missing OR pending)
    # ------------------------------------------------------------------
    risk = df[df["invoice_ready"] != True].copy()

    return risk


# =============================================================================
# NEW: OPERATIONAL INVOICE DASHBOARD LOGIC
# Replaces the static invoice_compliance matrix with a shipment-driven
# operational dashboard: Missing Bills / Pending Bills / Invoice Complete
# =============================================================================

import re
from rapidfuzz import process, fuzz


# -----------------------------------------------------------------------------
# HELPER: Normalize bill type (reused from build_dashboard_data.py)
# -----------------------------------------------------------------------------
VALID_TYPES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]

def normalize_bill_type(text):
    text = str(text).strip().upper() if pd.notna(text) else ""
    if text == "":
        return None
    if text == "OF" or "OCEAN" in text or "AIR FREIGHT" in text or "AIRFREIGHT" in text:
        return "OF"
    if "CUSTOM" in text:
        return "CUSTOMS"
    if "DUTY" in text:
        return "DUTY"
    if "DRAY" in text:
        return "DRAYAGE"
    # Fuzzy fallback for typos
    match = process.extractOne(text, VALID_TYPES, scorer=fuzz.ratio)
    if match and match[1] >= 85:
        return match[0]
    return None


# -----------------------------------------------------------------------------
# HELPER: Detect pending invoice placeholders
# Reuses the same pattern logic from QC snapshot (build_dashboard_data.py lines 646-648)
# -----------------------------------------------------------------------------
PENDING_RE = re.compile(
    r"^(PENDING|PENDNG|PENDIG|PENDIN|PEND|PENING|POSTED)$",
    re.IGNORECASE
)

def _is_pending_invoice(bill_inv):
    if pd.isna(bill_inv):
        return False
    val = str(bill_inv).strip().upper()
    return bool(PENDING_RE.match(val))


# -----------------------------------------------------------------------------
# CORE: Build per-container invoice status matrix for arriving shipments
# -----------------------------------------------------------------------------
def build_invoice_status_matrix(
    df_exec: pd.DataFrame,
    bills_df: pd.DataFrame,
    gl_bills_df: pd.DataFrame,
    shipment_mapping_df: pd.DataFrame,
    days_ahead: int = 2,
    days_back: int = 7
) -> pd.DataFrame:
    """
    Build a per-container invoice status matrix for containers arriving
    within the next `days_ahead` days (today through days_ahead inclusive).

    Returns DataFrame with one row per container+PO in the arrival window:
    - container, sipl, po_number, port_eta, supplier, final_destination
    - OF_status, CUSTOMS_status, DUTY_status, DRAYAGE_status (values: 'Actual', 'Pending', 'Missing')
    - missing_bills (comma-separated)
    - pending_bills (comma-separated)
    - invoice_complete (bool)
    - days_until_arrival (int)
    - arrival_status ('Today', 'Approaching', 'Past ETA')
    - invoice_completion_date (datetime if complete, else NaT)
    """
    REQUIRED = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]
    today = pd.Timestamp.today().normalize()
    soon_cutoff = today + pd.Timedelta(days=days_ahead)
    lookback_cutoff = today - pd.Timedelta(days=days_back)

    # -------------------------------------------------------------------------
    # 1. GET ON-WATER + RECENTLY ARRIVED CONTAINERS FROM EXECUTION LAYER
    #    Scope: everything still on the water (port_eta >= today), plus a
    #    lookback window (default 7 days) so recently-arrived containers with
    #    outstanding bills stay visible as "Past ETA" follow-ups.
    # -------------------------------------------------------------------------
    df = df_exec[df_exec["port_eta"].notna()].copy()
    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce")

    df = df[df["port_eta"] >= lookback_cutoff].copy()

    if df.empty:
        cols = ["container", "sipl", "po_number", "port_eta", "supplier", "final_destination",
                "OF_status", "CUSTOMS_status", "DUTY_status", "DRAYAGE_status",
                "missing_bills", "pending_bills", "invoice_complete",
                "days_until_arrival", "arrival_status", "invoice_completion_date"]
        return pd.DataFrame(columns=cols)

    # Arrival metadata
    df["days_until_arrival"] = (df["port_eta"].dt.normalize() - today).dt.days
    # Four mutually exclusive arrival buckets:
    #   Past ETA      → already arrived (within lookback), bills may be outstanding
    #   Today         → arriving today
    #   Arriving Soon → within the operational window (<= days_ahead)
    #   On Water      → still sailing, beyond the operational window
    df["arrival_status"] = "On Water"
    df.loc[df["port_eta"] <= soon_cutoff, "arrival_status"] = "Arriving Soon"
    df.loc[df["port_eta"].dt.normalize() == today, "arrival_status"] = "Today"
    df.loc[df["port_eta"] < today, "arrival_status"] = "Past ETA"

    # Ensure key columns exist
    for col in ["container_id", "sipl", "po_number", "supplier", "final_destination"]:
        if col not in df.columns:
            df[col] = pd.NA

    # Use container_id as primary container identifier
    df = df.rename(columns={"container_id": "container"})

    # -------------------------------------------------------------------------
    # 2. BUILD CONTAINER-LEVEL BILL INVENTORY FROM BILLS + GL
    # Mirrors build_dashboard_data.py Step 3 logic
    # -------------------------------------------------------------------------

    # A. Build SIPL -> Container master from bills (with note extraction fallback)
    master = (
        bills_df[bills_df["sipl_inv"].notna() & bills_df["container"].notna()]
        [["sipl_inv", "container"]]
        .drop_duplicates()
    )
    sipl_set = set(master["sipl_inv"].astype(str).str.upper())
    container_set = set(master["container"].astype(str).str.upper())

    def extract_sipl(note):
        if pd.isna(note):
            return None
        candidates = re.findall(r"(\d{5,6}[A-Z]?)", str(note).upper())
        matches = [x for x in candidates if x in sipl_set]
        return matches[0] if matches else None

    def extract_container(note):
        if pd.isna(note):
            return None
        candidates = re.findall(r"([A-Z]{4}\d{7})", str(note).upper())
        matches = [x for x in candidates if x in container_set]
        return matches[0] if matches else None

    bills_wk = bills_df.copy()
    bills_wk["sipl_from_notes"] = bills_wk["notes"].apply(extract_sipl)
    bills_wk["container_from_notes"] = bills_wk["notes"].apply(extract_container)

    bills_wk["sipl_final"] = bills_wk["sipl_inv"]
    mask = bills_wk["sipl_final"].isna() & bills_wk["sipl_from_notes"].notna()
    bills_wk.loc[mask, "sipl_final"] = bills_wk.loc[mask, "sipl_from_notes"]

    bills_wk["container_final"] = bills_wk["container"]
    mask = bills_wk["container_final"].isna() & bills_wk["container_from_notes"].notna()
    bills_wk.loc[mask, "container_final"] = bills_wk.loc[mask, "container_from_notes"]

    container_map = master.drop_duplicates("sipl_inv").set_index("sipl_inv")["container"]
    bills_wk["container_from_sipl"] = bills_wk["sipl_final"].map(container_map)
    mask = bills_wk["container_final"].isna() & bills_wk["container_from_sipl"].notna()
    bills_wk.loc[mask, "container_final"] = bills_wk.loc[mask, "container_from_sipl"]

    # Keep only container-linked bills
    container_bills = bills_wk[bills_wk["container_final"].notna()].copy()
    # Drop the raw source columns BEFORE renaming — otherwise renaming
    # container_final -> container duplicates the existing raw "container"
    # column and every downstream merge/str call breaks.
    container_bills = container_bills.drop(
        columns=["supplier", "sipl_inv", "container", "notes",
                 "sipl_from_notes", "container_from_notes", "container_from_sipl"],
        errors="ignore"
    )
    container_bills = container_bills.rename(
        columns={"sipl_final": "sipl", "container_final": "container"}
    )
    # Normalize keys for the merges below
    container_bills["container"] = container_bills["container"].astype(str).str.upper().str.strip()
    container_bills["sipl"] = container_bills["sipl"].astype(str).str.upper().str.strip()

    # B. Merge with GL bills to get description_clean
    gl_wk = gl_bills_df[gl_bills_df["type"] == "Bill"].copy()
    gl_wk["description_clean"] = gl_wk["description"].astype(str).str.upper().str.strip()
    gl_wk.loc[
        gl_wk["description_clean"].str.contains(r"PO", case=False, na=False),
        "description_clean"
    ] = pd.NA
    gl_wk["description_clean"] = gl_wk["description_clean"].replace({
        "OCEAN FREIGHT": "OF",
        "AIR FREIGHT": "OF",
        "MIS": "MISC"
    })
    gl_wk = gl_wk[gl_wk["description_clean"].notna()].copy()

    # Merge bills -> GL on invoice number
    matched = container_bills.merge(
        gl_wk,
        left_on="bill_inv",
        right_on="invoice",
        how="left"
    )

    # C. Merge shipment mapping for PO number
    # NOTE: select the cleaned columns FIRST, then rename. shipment_mapping
    # already contains raw "container"/"sipl" columns alongside the cleaned
    # "container_id"/"sipl_number" — renaming without selecting first creates
    # duplicate column names, which makes sm["container"] a DataFrame and
    # crashes .str accessor calls.
    sm = shipment_mapping_df[["container_id", "sipl_number", "po_number"]].rename(
        columns={"container_id": "container", "sipl_number": "sipl"}
    ).drop_duplicates()
    sm["container"] = sm["container"].astype(str).str.upper().str.strip()
    sm["sipl"] = sm["sipl"].astype(str).str.upper().str.strip()

    matched = matched.merge(sm, on=["container", "sipl"], how="left")

    # D. Normalize bill types and detect pending
    matched["bill_type"] = matched["description_clean"].apply(normalize_bill_type)
    matched["is_pending"] = matched["bill_inv"].apply(_is_pending_invoice)

    # Keep only valid bill types
    compliance_bills = matched[matched["bill_type"].notna()].copy()

    # -------------------------------------------------------------------------
    # 3. BUILD PER-CONTAINER STATUS MATRIX
    # -------------------------------------------------------------------------
    # For each container+PO from execution layer, determine status of each bill type
    exec_containers = df[["container", "po_number", "sipl", "port_eta",
                          "supplier", "final_destination", "days_until_arrival",
                          "arrival_status"]].drop_duplicates()

    status_rows = []

    for _, row in exec_containers.iterrows():
        container = row["container"]
        po = row["po_number"]

        # Get bills for this container+PO
        cb = compliance_bills[
            (compliance_bills["container"] == container) &
            (compliance_bills["po_number"] == po)
        ]

        status = {"container": container, "po_number": po}

        # Add execution metadata
        for col in ["sipl", "port_eta", "supplier", "final_destination",
                    "days_until_arrival", "arrival_status"]:
            status[col] = row[col]

        missing_list = []
        pending_list = []
        actual_dates = []

        for bill_type in REQUIRED:
            type_bills = cb[cb["bill_type"] == bill_type]

            if type_bills.empty:
                status[f"{bill_type}_status"] = "Missing"
                missing_list.append(bill_type)
            else:
                pending_bills = type_bills[type_bills["is_pending"]]
                actual_bills = type_bills[~type_bills["is_pending"]]

                if not pending_bills.empty:
                    status[f"{bill_type}_status"] = "Pending"
                    pending_list.append(bill_type)
                elif not actual_bills.empty:
                    status[f"{bill_type}_status"] = "Actual"
                    # Track completion date (latest actual invoice date)
                    if "invoice_dt" in actual_bills.columns:
                        actual_dates.extend(
                            pd.to_datetime(actual_bills["invoice_dt"], errors="coerce").dropna().tolist()
                        )
                else:
                    status[f"{bill_type}_status"] = "Missing"
                    missing_list.append(bill_type)

        status["missing_bills"] = ", ".join(missing_list) if missing_list else ""
        status["pending_bills"] = ", ".join(pending_list) if pending_list else ""
        status["invoice_complete"] = (len(missing_list) == 0 and len(pending_list) == 0)

        if actual_dates:
            status["invoice_completion_date"] = max(actual_dates)
        else:
            status["invoice_completion_date"] = pd.NaT

        status_rows.append(status)

    result = pd.DataFrame(status_rows)

    # Ensure all required columns exist
    for bill_type in REQUIRED:
        if f"{bill_type}_status" not in result.columns:
            result[f"{bill_type}_status"] = "Missing"

    return result


# -----------------------------------------------------------------------------
# CLASSIFY CONTAINERS INTO THREE MUTUALLY EXCLUSIVE TABS
# Priority: Pending -> Missing -> Complete
# -----------------------------------------------------------------------------
def classify_container_invoice_status(status_matrix: pd.DataFrame) -> tuple:
    """
    Classify each container into exactly ONE of three categories:
    1. PENDING - has at least one pending bill (work initiated)
    2. MISSING - no pending bills, but has at least one missing bill
    3. COMPLETE - all four bills are Actual

    Returns: (pending_df, missing_df, complete_df)
    """
    if status_matrix.empty:
        empty_cols = status_matrix.columns.tolist() if not status_matrix.empty else [
            "container", "sipl", "po_number", "port_eta", "supplier", "final_destination",
            "OF_status", "CUSTOMS_status", "DUTY_status", "DRAYAGE_status",
            "missing_bills", "pending_bills", "invoice_complete",
            "days_until_arrival", "arrival_status", "invoice_completion_date"
        ]
        empty_df = pd.DataFrame(columns=empty_cols)
        return empty_df, empty_df, empty_df

    df = status_matrix.copy()

    # Priority 1: Pending (has any pending bill)
    pending_mask = df["pending_bills"] != ""
    pending_df = df[pending_mask].copy()

    # Priority 2: Missing (no pending, but has missing)
    missing_mask = (df["pending_bills"] == "") & (df["missing_bills"] != "")
    missing_df = df[missing_mask].copy()

    # Priority 3: Complete (no pending, no missing)
    complete_mask = (df["pending_bills"] == "") & (df["missing_bills"] == "")
    complete_df = df[complete_mask].copy()

    # Verify mutual exclusivity and completeness
    total_classified = len(pending_df) + len(missing_df) + len(complete_df)
    assert total_classified == len(df), "Classification mismatch!"

    return pending_df, missing_df, complete_df


# -----------------------------------------------------------------------------
# MAIN FUNCTION: GET OPERATIONAL INVOICE DASHBOARD DATA
# -----------------------------------------------------------------------------
def get_operational_invoice_dashboard(
    df_exec: pd.DataFrame,
    bills_df: pd.DataFrame,
    gl_bills_df: pd.DataFrame,
    shipment_mapping_df: pd.DataFrame,
    days_ahead: int = 2,
    days_back: int = 7
) -> dict:
    """
    Main entry point for the Invoice Compliance operational dashboard.

    Returns dict with three DataFrames:
    - 'missing_bills': Containers arriving in window with missing bills (no pending)
    - 'pending_bills': Containers arriving in window with pending bills
    - 'invoice_complete': Containers arriving in window with all 4 bills received

    Also includes 'kpis' dict with summary metrics.
    """
    # Build the status matrix (all on-water containers + recent past-ETA lookback)
    status_matrix = build_invoice_status_matrix(
        df_exec, bills_df, gl_bills_df, shipment_mapping_df, days_ahead, days_back
    )

    # Classify into three tabs
    pending_df, missing_df, complete_df = classify_container_invoice_status(status_matrix)

    # Calculate KPIs
    total_containers = len(status_matrix)
    on_water_statuses = ["Today", "Arriving Soon", "On Water"]
    containers_on_water = (
        status_matrix[status_matrix["arrival_status"].isin(on_water_statuses)]["container"].nunique()
        if total_containers > 0 else 0
    )
    arriving_window = (
        status_matrix[status_matrix["arrival_status"].isin(["Today", "Arriving Soon"])]["container"].nunique()
        if total_containers > 0 else 0
    )
    kpis = {
        "containers_on_water": containers_on_water,
        "containers_arriving": arriving_window,
        "invoice_complete": len(complete_df),
        "containers_missing_bills": len(missing_df),
        "pending_bills": len(pending_df),
        "invoice_completion_pct": round(len(complete_df) / total_containers * 100, 1) if total_containers > 0 else 0,
        "avg_missing_per_container": round(
            status_matrix["missing_bills"].apply(lambda x: len(x.split(", ")) if x else 0).mean(), 1
        ) if total_containers > 0 else 0,
    }

    return {
        "missing_bills": missing_df,
        "pending_bills": pending_df,
        "invoice_complete": complete_df,
        "kpis": kpis,
        "status_matrix": status_matrix  # full matrix for debugging/export
    }