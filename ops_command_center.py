# -*- coding: utf-8 -*-
"""
OPS COMMAND CENTER
One page, ordered by urgency, built for the operations team to catch
problems before they land and resolve them — not a data-source-by-data-
source report (that's what the Bills/GL/Container tabs elsewhere are for).
Reads dashboard_data.xlsx, produced by build_dashboard_data.py.

Layout (top to bottom, deliberately no tabs):
  1. Act Now        — exceptions, LFD breaches, missing invoices at port,
                       documentation stuck while already at port
  2. This Week      — upcoming arrivals, LFD approaching, documentation
                       risk for arrivals within 7 days, missing invoices
                       arriving soon
  3. Invoice Health — compliance rate, Missing/Pending by category
  4. Pipeline Overview — where's everything stuck, one glance
  5. Look Up a Container — search

Deliberately reuses logic_cloud.py's existing, already-verified functions
(build_execution_master, get_lfd_risk, get_port_eta_doc_risk, the arrival-
window counts) rather than reimplementing them — those were confirmed
correct earlier this session (their real bodies had been trapped as dead
code by a copy-paste bug and were recovered, not fundamentally flawed).
Cuts vendor-concentration/trade-lane/transit-time analysis and the
source-based tab structure — useful for a monthly review, not a daily
triage tool.

Run: streamlit run ops_command_center.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.express as px
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from logic_cloud import (
    build_execution_master, get_containers_on_water, get_arriving_today,
    get_location_reached, get_next_7_days_arrivals, get_location_next_7_days,
    get_lfd_risk, get_port_eta_doc_risk,
)

st.set_page_config(page_title="Ops Command Center", layout="wide")

EXCEPTION_STATES = ["ON HOLD", "DAMAGED", "ON EXAM"]
CATEGORY_LABELS = {"OF": "Ocean Freight", "CUSTOMS": "Customs", "DUTY": "Duty", "DRAYAGE": "Drayage"}


def _fmt(df):
    """Dates as YYYY-MM-DD for display, not datetime-with-time noise."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    return df


st.markdown(
    "<h1 style='text-align: center; color: #2c3e50;'>🎯 OPERATIONS COMMAND CENTER</h1>"
    "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
    "One page, ordered by urgency — act now, then this week, then the full picture</p>",
    unsafe_allow_html=True
)

uploaded_file = st.sidebar.file_uploader("Upload dashboard_data.xlsx", type=["xlsx"])
if uploaded_file is None:
    st.info("📁 Upload dashboard_data.xlsx to get started (run `python build_dashboard_data.py` first if you don't have it).")
    st.stop()

xls = pd.ExcelFile(uploaded_file)
in_transit_raw = pd.read_excel(xls, "in_transit")
inventory_raw = pd.read_excel(xls, "inventory_intransit")
shipment_mapping_raw = pd.read_excel(xls, "shipment_mapping")
invoice_compliance_raw = (
    pd.read_excel(xls, "invoice_compliance") if "invoice_compliance" in xls.sheet_names else pd.DataFrame()
)

df_exec = build_execution_master(in_transit_raw, inventory_raw, shipment_mapping_raw)
df_exec["sipl_status_clean"] = df_exec["sipl_status"].astype(str).str.upper().str.strip()

ic = invoice_compliance_raw.copy()
if not ic.empty:
    ic.columns = ic.columns.str.strip()
    # Scoped to rows with a real container — SIPLs with none (domestic/
    # parcel moves) can't be correlated to any bill in our container-keyed
    # compliance model, and would otherwise silently drag every rate down.
    ic_scoped = ic[ic["container"].notna()]
else:
    ic_scoped = ic

# =============================================================================
# PRECOMPUTE — all figures verified against real data before wiring into UI
# =============================================================================
on_water = get_containers_on_water(df_exec)
port_7d = get_next_7_days_arrivals(df_exec)
branch_7d = get_location_next_7_days(df_exec)

# .dropna(subset=["container_id"]) BEFORE drop_duplicates everywhere below —
# drop_duplicates() treats NaN as its own group, so without this a single
# row with no container_id gets counted as one phantom "container".
exceptions = df_exec[df_exec["sipl_status_clean"].isin(EXCEPTION_STATES)].dropna(subset=["container_id"]).drop_duplicates("container_id")

breach_df, approaching_df = get_lfd_risk(df_exec)
breach_df = breach_df.dropna(subset=["container_id"]).drop_duplicates("container_id")
approaching_df = approaching_df.dropna(subset=["container_id"]).drop_duplicates("container_id")

doc_risk = get_port_eta_doc_risk(df_exec)
doc_already = doc_risk[doc_risk["risk_type"] == "Already at Port"].dropna(subset=["container_id"]).drop_duplicates("container_id")
doc_upcoming = doc_risk[doc_risk["risk_type"] == "Upcoming"].dropna(subset=["container_id"]).drop_duplicates("container_id")

if not ic_scoped.empty:
    urgent_missing = ic_scoped[
        (ic_scoped["overall_status"] == "Missing") & (ic_scoped["arrival_status"].isin(["Past ETA", "Today"]))
    ]
    # "Approaching" in this schema is an unbounded catch-all (any future
    # ETA), not "within 7 days" — bound it explicitly via days_to_port_eta.
    week_missing = ic_scoped[
        (ic_scoped["overall_status"] == "Missing")
        & (ic_scoped["days_to_port_eta"] > 0) & (ic_scoped["days_to_port_eta"] <= 7)
    ]
else:
    urgent_missing = pd.DataFrame(columns=["container"])
    week_missing = pd.DataFrame(columns=["container"])

# =============================================================================
# HEADER KPI STRIP
# =============================================================================
st.divider()
cols = st.columns(6)
cols[0].metric("🌊 On Water", on_water)
cols[1].metric("🚨 Exceptions", len(exceptions))
cols[2].metric("🔴 LFD Breach", len(breach_df))
cols[3].metric("🧾 Missing (Urgent)", urgent_missing["container"].nunique())
cols[4].metric("📋 Doc Risk (At Port)", len(doc_already))
cols[5].metric("📅 Arriving This Week", port_7d)

# =============================================================================
# SECTION 1: ACT NOW
# =============================================================================
st.divider()
st.markdown("## 🔴 Act Now")
st.caption("If something here needs a decision, that's the point of this section.")

st.markdown("### 🚨 Exceptions")
if len(exceptions) > 0:
    st.error(f"{len(exceptions)} container(s) on hold, damaged, or under exam.")
    st.dataframe(
        _fmt(exceptions[["container_id", "sipl", "sipl_status", "port_eta", "supplier"]]),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No containers currently on hold, damaged, or under exam.")

st.markdown("### 🔴 LFD Breach — Delivery Scheduled Past the Deadline")
if len(breach_df) > 0:
    st.error(f"{len(breach_df)} container(s) have a branch delivery date scheduled AFTER their LFD.")
    st.dataframe(
        _fmt(breach_df[["container_id", "lfd", "delivery_eta", "sipl_status"]]),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No delivery-vs-LFD conflicts on file.")

st.markdown("### 🧾 Missing Invoices — Container Already At/Past Port")
if len(urgent_missing) > 0:
    st.error(f"{urgent_missing['container'].nunique()} container(s) already at port with missing invoices.")
    st.dataframe(
        urgent_missing[["container", "sipl", "port_eta", "arrival_status", "missing_categories", "freight_forwarder"]],
        use_container_width=True, hide_index=True
    )
else:
    st.success("No missing invoices on containers already at port.")

st.markdown("### 📋 Documentation Risk — Already at Port")
if len(doc_already) > 0:
    st.error(f"{len(doc_already)} container(s) at port but still stuck on early paperwork.")
    st.dataframe(
        _fmt(doc_already[["container_id", "sipl_status", "port_eta"]]),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No containers at port with paperwork behind schedule.")

# =============================================================================
# SECTION 2: THIS WEEK
# =============================================================================
st.divider()
st.markdown("## 🟡 This Week")
st.caption("Nothing urgent yet — just what to have on your radar.")

col1, col2 = st.columns(2)
col1.metric("📍 Reaching Port (7 days)", port_7d)
col2.metric("🏢 Reaching Branch (7 days)", branch_7d)

st.markdown("### 🟡 LFD Approaching (Next 3 Days)")
if len(approaching_df) > 0:
    st.warning(f"{len(approaching_df)} container(s) have an LFD in the next 3 days, not yet delivered.")
    st.dataframe(
        _fmt(approaching_df[["container_id", "lfd", "delivery_eta", "sipl_status"]]),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No LFD deadlines approaching in the next 3 days.")

st.markdown("### 📋 Documentation Risk — Arriving Soon")
if len(doc_upcoming) > 0:
    st.warning(f"{len(doc_upcoming)} container(s) arriving within 7 days still stuck on early paperwork.")
    st.dataframe(
        _fmt(doc_upcoming[["container_id", "sipl_status", "port_eta"]].sort_values("port_eta")),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No upcoming arrivals with paperwork behind schedule.")

st.markdown("### 🧾 Missing Invoices — Arriving Within 7 Days")
if len(week_missing) > 0:
    st.warning(f"{week_missing['container'].nunique()} container(s) arriving this week with missing invoices — start chasing now.")
    st.dataframe(
        week_missing[["container", "sipl", "port_eta", "days_to_port_eta", "missing_categories", "freight_forwarder"]].sort_values("port_eta"),
        use_container_width=True, hide_index=True
    )
else:
    st.success("No missing invoices on containers arriving this week.")

# =============================================================================
# SECTION 3: INVOICE HEALTH
# =============================================================================
st.divider()
st.markdown("## 🧾 Invoice Health")

if not ic_scoped.empty:
    container_overall = ic_scoped.drop_duplicates("container").set_index("container")["overall_status"]
    compliance_pct = 100 * (container_overall == "Complete").sum() / len(container_overall) if len(container_overall) else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Compliance Rate", f"{compliance_pct:.1f}%", help="Containers fully Complete across all 4 categories")
    col2.metric("Missing", int((container_overall == "Missing").sum()))
    col3.metric("Pending", int((container_overall == "Pending").sum()))
    col4.metric("Complete", int((container_overall == "Complete").sum()))

    cat_rows = []
    ic_dedup = ic_scoped.drop_duplicates("container")
    for cat, label in CATEGORY_LABELS.items():
        col = f"{cat}_status"
        if col in ic_dedup.columns:
            vc = ic_dedup[col].value_counts()
            cat_rows.append({"Category": label, "Complete": vc.get("Complete", 0), "Pending": vc.get("Pending", 0), "Missing": vc.get("Missing", 0)})

    if cat_rows:
        cat_df = pd.DataFrame(cat_rows).set_index("Category")
        col1, col2 = st.columns([1, 1])
        with col1:
            st.dataframe(cat_df, use_container_width=True)
        with col2:
            fig = px.bar(
                cat_df, x=cat_df.index, y=["Complete", "Pending", "Missing"], barmode="stack",
                color_discrete_map={"Complete": "#2ecc71", "Pending": "#f39c12", "Missing": "#e74c3c"}
            )
            st.plotly_chart(fig, use_container_width=True)

    with st.expander(f"See all Missing invoices ({int((ic_scoped['overall_status'] == 'Missing').sum())} rows)"):
        st.dataframe(
            ic_scoped[ic_scoped["overall_status"] == "Missing"][
                ["container", "sipl", "port_eta", "arrival_status", "missing_categories", "freight_forwarder", "vendor_to_follow_up"]
            ],
            use_container_width=True, hide_index=True
        )
    with st.expander(f"See all Pending invoices ({int((ic_scoped['overall_status'] == 'Pending').sum())} rows)"):
        st.dataframe(
            ic_scoped[ic_scoped["overall_status"] == "Pending"][
                ["container", "sipl", "port_eta", "arrival_status", "pending_categories", "vendor_to_follow_up"]
            ],
            use_container_width=True, hide_index=True
        )
else:
    st.info("No invoice_compliance data in this file — run build_dashboard_data.py first.")

# =============================================================================
# SECTION 4: PIPELINE OVERVIEW
# =============================================================================
st.divider()
st.markdown("## 📦 Pipeline Overview")
st.caption("Where's everything stuck, at a glance.")

pipeline_df = df_exec[df_exec["container_id"].notna()].drop_duplicates("container_id")
status_counts = pipeline_df["sipl_status"].value_counts()

col1, col2 = st.columns([2, 1])
with col1:
    fig = px.bar(x=status_counts.values, y=status_counts.index, orientation="h",
                 labels={"x": "# Containers", "y": "Status"}, title="Containers by Pipeline Status")
    st.plotly_chart(fig, use_container_width=True)
with col2:
    st.dataframe(status_counts.rename("Containers"), use_container_width=True)

# =============================================================================
# SECTION 5: LOOK UP A CONTAINER
# =============================================================================
st.divider()
st.markdown("## 🔍 Look Up a Container")

search = st.text_input("Enter a container number:", "").strip().upper()
if search:
    matches = df_exec[df_exec["container_id"] == search]
    if len(matches) == 0:
        st.error(f"No data found for {search}.")
    else:
        st.success(f"Found {len(matches)} SIPL record(s) for {search}")
        st.dataframe(
            _fmt(matches[["sipl", "sipl_status", "port_eta", "delivery_eta", "lfd", "ship_b_l_date", "supplier", "forwarder"]]),
            use_container_width=True, hide_index=True
        )

        if not ic_scoped.empty:
            ic_match = ic_scoped[ic_scoped["container"] == search]
            if len(ic_match) > 0:
                st.markdown("**Invoice status for this container:**")
                row = ic_match.iloc[0]
                icols = st.columns(4)
                for i, (cat, label) in enumerate(CATEGORY_LABELS.items()):
                    icols[i].metric(label, row.get(f"{cat}_status", "Unknown"))

st.divider()
st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Source: uploaded dashboard_data.xlsx")
