# -*- coding: utf-8 -*-
"""
logic_cloud.py — Business logic / KPI layer for the Logistics Control Tower.

REBUILT FROM THE PREVIOUS VERSION. Three findings drove this rewrite,
not a stylistic preference:

1. THREE FUNCTIONS WERE SILENTLY BROKEN. In the previous file,
   get_eta_performance(), get_container_data_issues(), and get_lfd_risk()
   each had their real body accidentally replaced by a copy-pasted
   "import pandas as pd / import re / import numpy as np / def
   clean_container_strict(val): ..." block. Because a Python function's
   body ends the moment indentation returns to column 0, each of these
   three functions' ACTUAL body was just one import statement — they
   returned None on every call. Their real logic (booking/ETA delay
   math, container data-quality checks, LFD breach/approaching risk) was
   still present in the file, but as dead, unreachable code sitting
   inside a spurious redefinition of clean_container_strict() that
   followed. Confirmed via `ast.parse` + walking the function table, not
   guesswork. This rebuild recovers that logic and reattaches it to the
   correct function.
2. clean_container_strict() was defined 4 separate times (identical
   body each time), each preceded by the same broken copy-paste pattern
   above — the actual cause of finding #1. Consolidated into one import
   from utils.clean_container per the project's own engineering rule:
   "Never duplicate business logic."
3. get_arriving_invoice_risk() assumed the OLD invoice_compliance schema
   (container+po_number grain, {CATEGORY}_ACTUAL/_PENDING booleans,
   invoice_ready boolean). The rebuilt ETL scores compliance per-SIPL
   with a different column set (see build_dashboard_data.py STEP 6) per
   an updated business decision to rely on GL only, not manually-tagged
   processor logs — so this function is rewritten against that schema,
   preserving the same OUTPUT contract app_cloud.py already consumes
   (container_id, po_number, arrival_status, port_eta, missing_bills,
   pending_bills, invoice_ready) so the presentation layer didn't also
   need a rewrite.

One thing deliberately NOT "fixed": the previous is_booking check in
app_cloud.py (`event_status == "BOOKED"`) looked buggy in isolation, but
load_bookings() below uppercases event_status before it ever reaches
app_cloud.py — so that comparison is correct. Flagging this so nobody
"fixes" a bug that doesn't exist.
"""

import re
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import clean_container as clean_container_strict  # single source of truth

REQUIRED_CATEGORIES = ["OF", "CUSTOMS", "DUTY", "DRAYAGE"]


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
        .replace({"NAN": np.nan})
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
    """Load bookings and ensure container IDs are valid ISO-6346 codes.

    The raw bookings may contain free-form text in the ``container_id``
    column — e.g. ``TRUCK 12`` or ``L&S`` — which should be ignored. This
    normalises the column, then coalesces any missing values from the
    shipment mapping, finally cleaning the result so *only* proper
    container numbers remain (``AAAA1234567``). Invalid values are NaN.
    """
    df = bookings_df.copy()

    df["event_status"] = (
        df["event_status"]
        .astype(str)
        .str.strip()
        .str.upper()
    )
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce", format="mixed")
    df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")
    if "container_id" in df.columns:
        df["container_id"] = df["container_id"].apply(clean_container_strict)

    mapping = load_shipment_mapping(shipment_mapping_df)

    # A PO can legitimately span multiple containers (confirmed: 75 of 849
    # PO numbers in shipment_mapping map to more than one distinct
    # container). Joining bookings to mapping on po_number alone would fan
    # out those 75 POs' event rows across every candidate container —
    # inflating a 760-row bookings table to ~60,000 rows and corrupting
    # every downstream count/sum KPI. We only use the mapping to fill a
    # missing container_id when the PO maps to EXACTLY one container;
    # otherwise there's no way to disambiguate which container a given
    # booking event belongs to, so it's left missing rather than guessed.
    map_slim = mapping[["po_number", "container_id"]].dropna().drop_duplicates()
    single_container_po = map_slim.groupby("po_number")["container_id"].nunique()
    unambiguous_po = single_container_po[single_container_po == 1].index
    map_slim = map_slim[map_slim["po_number"].isin(unambiguous_po)]

    df = df.merge(
        map_slim,
        on="po_number",
        how="left",
        suffixes=("", "_map")
    )

    if "container_id_map" in df.columns:
        df["container_id"] = df["container_id"].combine_first(df["container_id_map"])
        df = df.drop(columns=["container_id_map"])
    if "container_id" in df.columns:
        df["container_id"] = df["container_id"].apply(clean_container_strict)
    return df


# -----------------------------------------------------------------------------
# ROLLOVER — TRUE EVENT COUNT
# -----------------------------------------------------------------------------
def get_rollover_summary(bookings):
    df = bookings.copy()
    df = df[df["event_status"].str.contains("ROLL", na=False)]
    df = df.drop_duplicates(subset=["container_id", "event_date", "vessel", "etd"])
    return df.groupby("container_id").size().reset_index(name="rollover_count")


# -----------------------------------------------------------------------------
# FINAL EVENT DATE
# -----------------------------------------------------------------------------
def get_container_final_date(bookings):
    df = bookings.copy()
    return (
        df.sort_values("event_date")
        .groupby("container_id")["event_date"]
        .last()
        .reset_index(name="final_event_date")
    )


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
    df["latest_etd"] = pd.to_datetime(df["latest_etd"], format="mixed")
    selected_date = pd.to_datetime(selected_date).normalize()
    sailed = df[df["latest_etd"].dt.normalize() == selected_date]
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
    TARGET_CATEGORIES = ["PQ SAMPLES", "PENTAL QUARTZ", "NATURAL STONE"]

    df_filtered = df[
        (df["po_status"] == TARGET_STATUS) &
        (df["category"].isin(TARGET_CATEGORIES))
    ]

    total_pos = df_filtered["po"].nunique()
    category_breakdown = df_filtered["category"].value_counts().reset_index()
    category_breakdown.columns = ["category", "count"]
    return total_pos, category_breakdown


# -----------------------------------------------------------------------------
# CONTAINER MASTER
# -----------------------------------------------------------------------------
def build_container_master(bookings):
    df = bookings[bookings["container_id"].notna()].copy()

    start_etd = df.groupby("container_id")["etd"].min()
    latest_etd = df.sort_values("event_date").groupby("container_id")["etd"].last()
    promised_eta = df.sort_values("event_date").groupby("container_id")["eta"].last()
    po_count = df.groupby("container_id")["po_number"].nunique()

    container_master = pd.DataFrame({
        "start_etd": start_etd,
        "latest_etd": latest_etd,
        "promised_eta": promised_eta,
        "po_count": po_count
    }).reset_index()

    container_master = container_master.merge(get_rollover_summary(bookings), on="container_id", how="left")
    container_master = container_master.merge(get_container_final_date(bookings), on="container_id", how="left")
    container_master["rollover_count"] = container_master["rollover_count"].fillna(0)
    return container_master


# -----------------------------------------------------------------------------
# KPI — CONTAINERS BY DESTINATION (BOOKED ONLY, NOT INSPECTION APPROVED)
# -----------------------------------------------------------------------------
def get_containers_by_destination(bookings, open_po_df):
    open_po = open_po_df.copy()
    open_po.columns = [c.lower().strip() for c in open_po.columns]

    open_po["po"] = pd.to_numeric(open_po["po"], errors="coerce").astype("Int64")
    open_po["po_status"] = open_po["po_status"].astype(str).str.strip().str.upper()
    open_po["ship_to_location"] = open_po["ship_to_location"].astype(str).str.strip()

    booked = bookings[
        (bookings["container_id"].notna()) & (bookings["etd"].notna())
    ][["container_id", "po_number"]].drop_duplicates()

    df = booked.merge(
        open_po[["po", "ship_to_location", "po_status"]],
        left_on="po_number", right_on="po", how="left"
    )
    df = df[df["po_status"] != "INSPECTION REPORT APPROVED"]

    return (
        df.drop_duplicates(subset=["container_id", "ship_to_location"])
        .groupby("ship_to_location")["container_id"]
        .nunique()
        .reset_index(name="container_count")
        .sort_values("container_count", ascending=False)
    )


# -----------------------------------------------------------------------------
# KPI — CONTAINERS SCHEDULED TO SAIL (MONTH)
# -----------------------------------------------------------------------------
def get_containers_sailed_by_month(df_master, selected_month):
    df = df_master.copy()
    df["latest_etd"] = pd.to_datetime(df["latest_etd"], format="mixed")
    df["month"] = df["latest_etd"].dt.to_period("M")
    selected_month = pd.Period(selected_month)
    return df[df["month"] == selected_month]["container_id"].nunique()


# -----------------------------------------------------------------------------
# FIRST BOOKING DATE (TRUE BOOKING MOMENT)
# -----------------------------------------------------------------------------
def get_container_first_booking(bookings):
    df = bookings.copy()
    df = df[df["container_id"].notna()]
    df = df[df["etd"].notna()]
    return (
        df.sort_values("event_date")
        .groupby("container_id")["event_date"]
        .min()
        .reset_index(name="first_booking_date")
    )


# -----------------------------------------------------------------------------
# CONTAINERS BOOKED IN DATE RANGE (FLOW METRIC)
# -----------------------------------------------------------------------------
def get_containers_booked_in_range(bookings, start_date, end_date):
    first_booking = get_container_first_booking(bookings)
    start_date, end_date = pd.to_datetime(start_date), pd.to_datetime(end_date)
    df = first_booking[
        (first_booking["first_booking_date"] >= start_date) &
        (first_booking["first_booking_date"] <= end_date)
    ]
    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# ACTIVE CONTAINERS IN DATE RANGE (ACTIVITY METRIC)
# -----------------------------------------------------------------------------
def get_active_containers(bookings, start_date, end_date):
    df = bookings.copy()
    start_date, end_date = pd.to_datetime(start_date), pd.to_datetime(end_date)
    df = df[(df["event_date"] >= start_date) & (df["event_date"] <= end_date)]
    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# CONTAINERS SCHEDULED TO SAIL IN DATE RANGE (PLANNED FLOW)
# -----------------------------------------------------------------------------
def get_containers_sailed_in_range(df_master, start_date, end_date):
    df = df_master.copy()
    df["latest_etd"] = pd.to_datetime(df["latest_etd"], format="mixed")
    start_date, end_date = pd.to_datetime(start_date), pd.to_datetime(end_date)
    df = df[(df["latest_etd"] >= start_date) & (df["latest_etd"] <= end_date)]
    return df["container_id"].nunique()


# -----------------------------------------------------------------------------
# PIPELINE DESTINATION (UNBOOKED)
# -----------------------------------------------------------------------------
def get_pipeline_destination(open_po_df):
    df = open_po_df.copy()
    df.columns = [c.lower().strip() for c in df.columns]
    df["po_status"] = df["po_status"].astype(str).str.strip().str.upper()
    df["ship_to_location"] = df["ship_to_location"].astype(str).str.strip()

    df = df[df["po_status"] == "INSPECTION REPORT APPROVED"]
    return (
        df.groupby("ship_to_location")["po"]
        .nunique()
        .reset_index(name="po_count")
        .sort_values("po_count", ascending=False)
    )


# =============================================================================
# ====================== EXECUTION + CONTROL TOWER LAYER =======================
# =============================================================================

def _coalesce(df, primary, fallback):
    if primary in df.columns and fallback in df.columns:
        return df[primary].fillna(df[fallback])
    elif primary in df.columns:
        return df[primary]
    elif fallback in df.columns:
        return df[fallback]
    else:
        return pd.Series(pd.NA, index=df.index, dtype=object)


def build_execution_master(in_transit_df, inventory_intransit_df, shipment_mapping_df):
    df_eta = in_transit_df.copy()
    df_inv = inventory_intransit_df.copy()
    df_map = shipment_mapping_df.copy()

    df_eta.columns = df_eta.columns.str.lower().str.strip()
    df_inv.columns = df_inv.columns.str.lower().str.strip()
    df_map.columns = df_map.columns.str.lower().str.strip()

    rename_eta = {
        "container": "container_eta",
        "supplier": "supplier_eta",
        "fr_forwarder": "forwarder_eta",
        "departure_port": "departure_port_eta",
        "ship_to_location": "final_destination",
        "location_eta": "delivery_eta",
    }
    rename_inv = {
        "container": "container_inv",
        "supplier": "supplier_inv",
        "freight_forwarder": "forwarder_inv",
        "departure_port": "departure_port_inv",
        "ship_to": "destination_inv",
        "eta_date": "delivery_eta_inv",
        "arrival_port": "arrival_port",
    }

    df_eta = df_eta.rename(columns={k: v for k, v in rename_eta.items() if k in df_eta.columns})
    df_inv = df_inv.rename(columns={k: v for k, v in rename_inv.items() if k in df_inv.columns})

    eta_keep = ["sipl", "supplier_eta", "container_eta", "forwarder_eta", "departure_port_eta",
                "final_destination", "port_eta", "delivery_eta", "lfd", "sipl_status"]
    inv_keep = ["sipl", "supplier_inv", "container_inv", "forwarder_inv", "departure_port_inv",
                "destination_inv", "delivery_eta_inv", "arrival_port", "ship_b_l_date"]

    df_eta = df_eta[[c for c in eta_keep if c in df_eta.columns]]
    df_inv = df_inv[[c for c in inv_keep if c in df_inv.columns]]

    df = pd.merge(df_eta, df_inv, on="sipl", how="outer")

    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce", format="mixed")
    df = df.sort_values("port_eta").drop_duplicates(subset=["sipl"], keep="last")

    df["container_id"] = _coalesce(df, "container_eta", "container_inv")
    df["supplier"] = _coalesce(df, "supplier_eta", "supplier_inv")
    df["forwarder"] = _coalesce(df, "forwarder_eta", "forwarder_inv")
    df["departure_port"] = _coalesce(df, "departure_port_eta", "departure_port_inv")
    df["final_destination"] = _coalesce(df, "final_destination", "destination_inv")
    df["delivery_eta"] = _coalesce(df, "delivery_eta", "delivery_eta_inv")

    drop_cols = ["container_eta", "container_inv", "supplier_eta", "supplier_inv",
                 "forwarder_eta", "forwarder_inv", "departure_port_eta", "departure_port_inv",
                 "destination_inv", "delivery_eta_inv"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    map_cols = [c for c in ["sipl_number", "container_id", "po_number"] if c in df_map.columns]
    df_map = df_map[map_cols].rename(columns={"sipl_number": "sipl"})
    df_map = df_map.rename(columns={"container_id": "container_id_map"})

    # shipment_mapping is unioned from several source pairings (open_po,
    # bookings, supplier_invoices, bills) — the same SIPL can appear twice,
    # once with a po_number filled in (e.g. from supplier_invoices) and
    # once with po_number null (e.g. from bills.xls, which never carries a
    # PO number). Left un-deduped, merging on "sipl" duplicates every
    # execution row for that SIPL. Confirmed with real data: SIPL 155507C
    # produced 2 identical rows in df_exec before this fix. Keep one row
    # per SIPL, preferring whichever has a non-null po_number.
    if "po_number" in df_map.columns:
        df_map = df_map.sort_values("po_number", na_position="first").drop_duplicates(subset=["sipl"], keep="last")
    else:
        df_map = df_map.drop_duplicates(subset=["sipl"])

    df = df.merge(df_map, on="sipl", how="left")
    if "container_id_map" in df.columns:
        df["container_id"] = df["container_id"].fillna(df["container_id_map"])
        df = df.drop(columns=["container_id_map"])

    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce", format="mixed")
    df["delivery_eta"] = pd.to_datetime(df["delivery_eta"], errors="coerce", format="mixed")
    df["lfd"] = pd.to_datetime(df["lfd"], errors="coerce", format="mixed")
    if "ship_b_l_date" in df.columns:
        df["ship_b_l_date"] = pd.to_datetime(df["ship_b_l_date"], errors="coerce", format="mixed")

    return df


# =============================================================================
# =============================== KPI LAYER ====================================
# =============================================================================

def get_total_containers(df):
    return df["container_id"].nunique()


def get_containers_on_water(df):
    today = pd.Timestamp.today().normalize()
    return df[df["port_eta"] >= today]["container_id"].nunique()


def get_arriving_today(df):
    today = pd.Timestamp.today().normalize()
    return df[df["port_eta"].dt.normalize() == today]["container_id"].nunique()


def get_current_week_arrivals(df):
    today = pd.Timestamp.today()
    current_week = today.isocalendar()[1]
    current_year = today.year
    mask = df["port_eta"].notna()
    iso = df.loc[mask, "port_eta"].dt.isocalendar()
    matched = df.loc[mask].loc[(iso["week"] == current_week) & (iso["year"] == current_year)]
    return matched["container_id"].nunique()


def get_next_7_days_arrivals(df):
    today = pd.Timestamp.today().normalize()
    return df[
        (df["port_eta"] >= today) & (df["port_eta"] <= today + pd.Timedelta(days=7))
    ]["container_id"].nunique()


def get_7day_not_ready(df):
    today = pd.Timestamp.today().normalize()
    if "sipl_status" not in df.columns:
        return 0
    ready_status = ["Scheduled for Delivery", "Delivery Pending", "At branch", "D.Os  Received"]
    return df[
        (df["port_eta"] >= today) &
        (df["port_eta"] <= today + pd.Timedelta(days=7)) &
        (~df["sipl_status"].isin(ready_status))
    ]["container_id"].nunique()


def get_invoice_needed(df):
    if "sipl_status" not in df.columns:
        return 0
    return df[df["sipl_status"].str.upper().str.contains("INVOICE", na=False)]["container_id"].nunique()


def get_location_reached(df):
    today = pd.Timestamp.today().normalize()
    return df[df["delivery_eta"].notna() & (df["delivery_eta"] <= today)]["container_id"].nunique()


def get_location_next_7_days(df):
    today = pd.Timestamp.today().normalize()
    return df[
        (df["delivery_eta"].notna()) &
        (df["delivery_eta"] >= today) &
        (df["delivery_eta"] <= today + pd.Timedelta(days=7))
    ]["container_id"].nunique()


def get_status_lag(df):
    today = pd.Timestamp.today().normalize()
    if "sipl_status" not in df.columns:
        return 0
    return df[
        (df["delivery_eta"].notna()) &
        (df["delivery_eta"] < today) &
        (~df["sipl_status"].isin(["At branch", "Scheduled for Delivery"]))
    ]["container_id"].nunique()


def get_lfd_breach(df):
    mask = df["lfd"].notna() & df["delivery_eta"].notna()
    return df[mask & (df["lfd"] < df["delivery_eta"])]["container_id"].nunique()


def get_eta_delay(open_po_df, df):
    booking = open_po_df[["po", "eta_port"]].copy()
    booking.columns = booking.columns.str.lower()
    booking.rename(columns={"eta_port": "promised_eta"}, inplace=True)
    booking["promised_eta"] = pd.to_datetime(booking["promised_eta"], errors="coerce", format="mixed")
    booking["po"] = pd.to_numeric(booking["po"], errors="coerce").astype("Int64")

    df = df.copy()
    df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")
    df = df.merge(booking, left_on="po_number", right_on="po", how="left")
    df["eta_delay"] = (df["port_eta"] - df["promised_eta"]).dt.days
    return df


def get_port_eta_doc_risk(df):
    df = df.copy()
    today = pd.Timestamp.today().normalize()

    df["sipl_status_clean"] = df["sipl_status"].astype(str).str.upper().str.strip()
    doc_stage_status = ["SIPL READY", "DOCUMENTS SENT", "NEED DOCUMENTS"]

    risk_df = df[
        (df["port_eta"].notna()) &
        (
            (df["port_eta"] <= today) |
            ((df["port_eta"] >= today) & (df["port_eta"] <= today + pd.Timedelta(days=7)))
        ) &
        (df["sipl_status_clean"].isin(doc_stage_status))
    ].copy()

    risk_df["risk_type"] = "Upcoming"
    risk_df.loc[risk_df["port_eta"] < today, "risk_type"] = "Already at Port"
    return risk_df


# -----------------------------------------------------------------------------
# PROMISED VS ACTUAL ETA ANALYSIS
# RECOVERED — this function's real body was previously dead code trapped
# inside a spurious clean_container_strict() redefinition (see module
# docstring). Reattached here, unchanged in substance.
# -----------------------------------------------------------------------------
def get_eta_performance(df_exec, bookings):
    bookings_clean = bookings.copy().sort_values("event_date")
    latest_booking = bookings_clean.groupby("container_id", as_index=False).last()
    latest_booking = latest_booking.rename(columns={
        "eta": "promised_eta",
        "fr_forwarder": "forwarder_booking",
        "etd": "booked_etd",
    })

    df = df_exec.merge(
        latest_booking[[c for c in
                         ["container_id", "booked_etd", "promised_eta", "forwarder_booking"]
                         if c in latest_booking.columns]],
        on="container_id", how="left",
    )

    for col in ["booked_etd", "promised_eta", "port_eta", "ship_b_l_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")

    df["booked_transit"] = (df["promised_eta"] - df["booked_etd"]).dt.days
    df["actual_transit"] = (df["port_eta"] - df["ship_b_l_date"]).dt.days
    df["shipping_delay"] = (df["ship_b_l_date"] - df["booked_etd"]).dt.days
    df["transit_delay"] = df["actual_transit"] - df["booked_transit"]

    if "ship_b_l_date" not in df.columns:
        raise ValueError("ship_b_l_date missing from execution layer — fix build_execution_master()")

    return df


# -----------------------------------------------------------------------------
# FIRST PO RECEIVED / BOOKING KPIs
# -----------------------------------------------------------------------------
def get_pos_received_in_range(bookings):
    df = bookings.copy()
    df = df[df["po_number"].notna()]
    return (
        df.sort_values("event_date")
        .groupby("po_number")["event_date"]
        .min()
        .reset_index(name="first_received_date")
    )


def get_pos_approved_in_range(bookings, start_date, end_date):
    df = bookings.copy()
    df = df[df["po_number"].notna()].copy()
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce", format="mixed")
    df["event_status"] = df["event_status"].astype(str).str.upper().str.strip()

    start_date, end_date = pd.to_datetime(start_date), pd.to_datetime(end_date)
    df = df[(df["event_date"] >= start_date) & (df["event_date"] <= end_date)]

    approved = df[df["event_status"] != "HOLD"]
    return approved["po_number"].nunique()


def get_po_to_container_ratio(bookings, start_date, end_date):
    approved_pos = get_pos_approved_in_range(bookings, start_date, end_date)

    df = bookings.copy()
    df = df[df["container_id"].notna()].copy()
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce", format="mixed")
    df["event_status"] = df["event_status"].astype(str).str.upper().str.strip()

    is_booking = (
        (df["event_status"] == "BOOKED") |
        ((df["event_status"] == "ROLLOVER") & df["container_id"].notna())
    )
    first_container = df[is_booking].groupby("container_id", as_index=False)["event_date"].min()

    start_date, end_date = pd.to_datetime(start_date), pd.to_datetime(end_date)
    container_in_range = first_container[
        (first_container["event_date"] >= start_date) & (first_container["event_date"] <= end_date)
    ]
    total_containers = container_in_range["container_id"].nunique()

    ratio = round(total_containers / approved_pos, 2) if approved_pos > 0 else 0
    return approved_pos, total_containers, ratio


def get_pos_without_container(bookings):
    df = bookings.copy()
    df = df[df["po_number"].notna()].copy()
    df["event_status"] = df["event_status"].astype(str).str.upper().str.strip()

    approved = df[df["event_status"] != "HOLD"]
    latest = approved.sort_values("event_date").groupby("po_number", as_index=False).last()
    no_container = latest[latest["container_id"].isna()]
    return no_container["po_number"].nunique(), no_container


# =============================================================================
# CONTAINER DATA VALIDATION (DESTINATION + PORT + DATA COMPLETENESS)
# RECOVERED — see module docstring. Real body was dead code trapped inside
# a spurious clean_container_strict() redefinition; reattached unchanged.
# =============================================================================
def get_container_data_issues(df_exec):
    df = df_exec.copy()

    if "container_id" not in df.columns and "container" in df.columns:
        df = df.rename(columns={"container": "container_id"})
    if "container_id" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    df = df[df["container_id"].notna()].copy()

    possible_fields = ["final_destination", "departure_port", "arrival_port",
                        "port_eta", "ship_b_l_date", "supplier", "freight_forwarder"]
    critical_fields = [c for c in possible_fields if c in df.columns]

    for col in ["final_destination", "departure_port", "arrival_port"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "none": pd.NA})

    def safe_nunique(series):
        return series.dropna().nunique()

    agg_dict = {}
    if "final_destination" in df.columns:
        agg_dict["destination_count"] = ("final_destination", safe_nunique)
    if "departure_port" in df.columns:
        agg_dict["departure_port_count"] = ("departure_port", safe_nunique)
    if "arrival_port" in df.columns:
        agg_dict["arrival_port_count"] = ("arrival_port", safe_nunique)

    container_check = df.groupby("container_id").agg(**agg_dict).reset_index()

    for col in ["destination_count", "departure_port_count", "arrival_port_count"]:
        if col not in container_check.columns:
            container_check[col] = 0

    def get_missing_fields(group):
        return [col for col in critical_fields if group[col].dropna().empty]

    missing_map = (
        df.groupby("container_id").apply(get_missing_fields, include_groups=False)
        .reset_index(name="missing_fields")
    )
    container_check = container_check.merge(missing_map, on="container_id", how="left")

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

    def classify_severity(row):
        missing = row["missing_fields"]
        if "final_destination" in missing or ("departure_port" in missing and "arrival_port" in missing):
            return "CRITICAL"
        if (row["destination_count"] > 1 or row["departure_port_count"] > 1 or
                row["arrival_port_count"] > 1 or "departure_port" in missing or "arrival_port" in missing):
            return "HIGH"
        if missing:
            return "MEDIUM"
        return ""

    container_check["severity"] = container_check.apply(classify_severity, axis=1)

    problem_containers = container_check[container_check["issue_type"] != ""]["container_id"]
    issues_df = df[df["container_id"].isin(problem_containers)].copy()
    issues_df = issues_df.merge(
        container_check[["container_id", "issue_type", "severity"]], on="container_id", how="left"
    )

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "": 3}
    issues_df["severity_rank"] = issues_df["severity"].map(severity_order)
    issues_df = issues_df.sort_values(
        by=["severity_rank", "container_id"], ascending=[True, True]
    ).drop(columns=["severity_rank"])

    return issues_df, container_check


# -----------------------------------------------------------------------------
# LFD RISK — BREACH + APPROACHING
# RECOVERED — see module docstring. Real body was dead code trapped inside
# a spurious clean_container_strict() redefinition; reattached unchanged.
# -----------------------------------------------------------------------------
def get_lfd_risk(df_exec, days_ahead=3):
    df = df_exec.copy()
    today = pd.Timestamp.today().normalize()

    for col in ["lfd", "delivery_eta"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")

    breach_df = df[
        (df["lfd"].notna()) & (df["delivery_eta"].notna()) & (df["delivery_eta"] > df["lfd"])
    ].copy()

    approaching_df = df[
        (df["lfd"].notna()) & (df["delivery_eta"].notna()) &
        (df["lfd"] >= today) & (df["lfd"] <= today + pd.Timedelta(days=days_ahead)) &
        (df["delivery_eta"] >= df["lfd"])
    ].copy()

    return breach_df, approaching_df


# -----------------------------------------------------------------------------
# ARRIVING / OUTSTANDING CONTAINERS WITH INVOICE ISSUES
#
# REWRITTEN against the new per-SIPL invoice_compliance schema (see
# build_dashboard_data.py STEP 6). The output CONTRACT is preserved so
# app_cloud.py's tab3 rendering (missing_bills / pending_bills /
# invoice_ready / arrival_status columns) keeps working unmodified:
#   arrival_status == "Approaching"  -> next `days` days
#   arrival_status == "Today"        -> arriving today
#   arrival_status == "Past ETA"     -> already passed, bills still outstanding
#   missing_bills  != ""             -> Missing Bills tab
#   pending_bills  != ""             -> Pending Bills tab
#
# Per the updated business decision (GL-only, no processor-log tagging),
# "pending" can no longer be attributed to a specific category — it's a
# signal that unposted bill activity exists, not a per-category status.
# pending_bills is populated with a generic marker rather than fabricating
# which of the 4 categories it covers.
# -----------------------------------------------------------------------------
def get_arriving_invoice_risk(df_exec, invoice_compliance, days=3):
    ALL_MISSING = ", ".join(REQUIRED_CATEGORIES)
    today = pd.Timestamp.today().normalize()

    df = df_exec[df_exec["port_eta"].notna()].copy()
    df["port_eta"] = pd.to_datetime(df["port_eta"], errors="coerce", format="mixed")

    df["arrival_status"] = "Approaching"
    df.loc[df["port_eta"].dt.normalize() == today, "arrival_status"] = "Today"
    df.loc[df["port_eta"] < today, "arrival_status"] = "Past ETA"

    if not invoice_compliance.empty and "sipl" in df.columns and "sipl" in invoice_compliance.columns:
        comp = invoice_compliance.rename(columns={"container": "container_id_compliance"})
        df = df.merge(comp, on="sipl", how="left", suffixes=("", "_compliance"))
        df["invoice_ready"] = df["overall_status"] == "Complete"
        df["missing_bills"] = df["missing_categories"].fillna(ALL_MISSING)
        df["pending_bills"] = np.where(
            df.get("has_uncategorized_pending_activity", False).fillna(False),
            "PENDING ACTIVITY", ""
        )
    else:
        df["invoice_ready"] = False
        df["missing_bills"] = ALL_MISSING
        df["pending_bills"] = ""

    no_data_mask = df["invoice_ready"].isna()
    df.loc[no_data_mask, "missing_bills"] = ALL_MISSING
    df.loc[no_data_mask, "pending_bills"] = ""
    df.loc[no_data_mask, "invoice_ready"] = False

    risk = df[df["invoice_ready"] != True].copy()
    return risk
