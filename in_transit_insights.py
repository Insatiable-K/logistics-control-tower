# -*- coding: utf-8 -*-
"""
IN-TRANSIT INSIGHTS DASHBOARD - Easy-to-Understand Version
The operational status board: where every shipment is right now, what
stage of the pipeline it's at, and what needs attention.

Runs standalone (`streamlit run in_transit_insights.py`) or as a tab inside
logistics_dashboard.py (which imports render_tab()).
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_html_table, clean_in_transit_dataframe


@st.cache_data
def load_and_clean_in_transit():
    """Load In-Transit List by SIPL.xls and clean it (shared logic in utils.py)."""
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    df, original_count, final_count = clean_in_transit_dataframe(raw)
    return df, original_count, final_count


def render_tab():
    """Render the full In-Transit Insights view. Callable standalone or as a tab."""

    sipl_df, original_rows, cleaned_rows = load_and_clean_in_transit()

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        This is the **operational status board** — every shipment currently
        moving, tracked by SIPL (our internal shipment reference number).

        Unlike the Bills/Accounting data, this file doesn't need a container
        number to be useful — the shipment's status and expected arrival date
        are valuable on their own. So nothing is dropped here for a missing
        container; it's flagged instead.

        **{cleaned_rows:,} shipments currently tracked.**
        """
    )

    # =========================================================================
    # WORKFLOW FUNNEL — "where is everything stuck"
    # =========================================================================
    st.divider()
    st.markdown("### 🚦 Where Is Everything Right Now? (Workflow Funnel)")

    st.markdown(
        """
        Every shipment sits at one specific stage of the delivery pipeline.
        This is the single most useful view in this file — it answers
        "where are our shipments stuck" at a glance.
        """
    )

    funnel_order = ["SIPL Ready", "Documents Sent", "Need Documents", "D.Os  Received",
                     "At branch", "Scheduled for Delivery", "On Hold"]
    status_counts = sipl_df["sipl_status"].value_counts()
    ordered = [s for s in funnel_order if s in status_counts.index]
    ordered += [s for s in status_counts.index if s not in ordered]
    status_counts = status_counts.reindex(ordered)

    col1, col2 = st.columns([2, 1])
    with col1:
        fig = px.bar(
            x=status_counts.values, y=status_counts.index, orientation="h",
            title="Shipments by Pipeline Stage",
            labels={"x": "# Shipments", "y": "Stage"}
        )
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**Stage counts:**")
        for stage, count in status_counts.items():
            pct = 100 * count / len(sipl_df)
            st.write(f"- **{stage}**: {count} ({pct:.0f}%)")

    st.markdown("**Coarse status (2-value summary):**")
    coarse = sipl_df["status"].value_counts()
    col1, col2 = st.columns(2)
    with col1:
        st.metric("In Transit", int(coarse.get("INTRANSIT", 0)))
    with col2:
        st.metric("ONSO Reduced", int(coarse.get("ONSO Reduced", 0)))

    # =========================================================================
    # LFD RISK — the most actionable finding in this file
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 Demurrage Risk: Last Free Day (LFD) Tracking")

    has_lfd = sipl_df["has_lfd"].sum()
    missing_lfd = len(sipl_df) - has_lfd
    missing_pct = 100 * missing_lfd / len(sipl_df)

    st.error(
        f"""
        **{missing_lfd} of {len(sipl_df)} shipments ({missing_pct:.0f}%) have no
        Last Free Day (LFD) on file.**

        💡 **Why this matters**: LFD is the deadline to pick up a container from
        port before daily storage/demurrage fees start accruing — it's the single
        most financially risky date in ocean freight. A shipment with no LFD on
        file isn't necessarily late, but it also isn't being actively monitored
        for this specific risk. Worth confirming whether LFD tracking happens
        elsewhere, or whether this is a real process gap.
        """
    )

    if has_lfd > 0:
        with st.expander(f"See the {has_lfd} shipments that DO have an LFD on file"):
            lfd_df = sipl_df[sipl_df["has_lfd"]][
                ["sipl", "container", "lfd", "sipl_status", "supplier"]
            ].sort_values("lfd")
            st.dataframe(lfd_df, use_container_width=True, hide_index=True)

    # =========================================================================
    # SIPL AGE — how long has this been sitting
    # =========================================================================
    st.divider()
    st.markdown("### ⏱️ Shipment Age (Days Since Initiated)")

    st.markdown(
        f"""
        **Average age: {sipl_df['sipl_age_days'].mean():.0f} days** |
        **Median: {sipl_df['sipl_age_days'].median():.0f} days**

        A shipment stuck at an early pipeline stage for a long time is worth a
        second look — the table below cross-references age against pipeline
        stage to surface exactly that.
        """
    )

    future_dated = (sipl_df["sipl_age_days"] < 0).sum()
    if future_dated > 0:
        st.info(
            f"ℹ️ **{future_dated} shipment(s) show a negative age** — meaning their "
            "'initiated' date is technically in the future. Likely a forward-dated "
            "entry or clock skew in the source system, not a real error, but flagged "
            "for completeness."
        )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(
            sipl_df, x="sipl_age_days", nbins=40,
            title="Distribution of Shipment Age",
            labels={"sipl_age_days": "Days Since Initiated", "count": "# Shipments"}
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Oldest shipments still active, by stage:**")
        stale = sipl_df[sipl_df["sipl_age_days"] >= 60].sort_values("sipl_age_days", ascending=False)
        st.write(f"{len(stale)} shipments have been open 60+ days")
        if len(stale) > 0:
            st.dataframe(
                stale[["sipl", "sipl_status", "sipl_age_days", "supplier"]].head(15),
                use_container_width=True, hide_index=True
            )

    # =========================================================================
    # FREIGHT FORWARDER CONCENTRATION
    # =========================================================================
    st.divider()
    st.markdown("### 🏢 Freight Forwarder Concentration")

    ff = sipl_df[sipl_df["fr_forwarder"].notna() & (sipl_df["fr_forwarder"] != "")]["fr_forwarder"]
    if len(ff) > 0:
        ff_counts = ff.value_counts()
        st.markdown(
            f"""
            **{len(ff)} of {len(sipl_df)} shipments** ({100*len(ff)/len(sipl_df):.0f}%) have a
            freight forwarder on file. Of those, the top 3 handle
            **{100*ff_counts.head(3).sum()/len(ff):.0f}%** of the volume — a real
            concentration worth knowing about for negotiating leverage and
            single-vendor risk.
            """
        )
        fig = px.bar(x=ff_counts.values, y=ff_counts.index, orientation="h",
                     title="Shipments by Freight Forwarder")
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # GLOBAL SOURCING FOOTPRINT
    # =========================================================================
    st.divider()
    st.markdown("### 🌍 Global Sourcing Footprint")

    dp = sipl_df[sipl_df["departure_port"].notna() & (sipl_df["departure_port"] != "")]["departure_port"]
    if len(dp) > 0:
        dp_counts = dp.value_counts()
        st.markdown(
            f"Of the **{len(dp)} shipments** with a known departure port, "
            f"here's where they're coming from:"
        )
        fig = px.bar(x=dp_counts.values, y=dp_counts.index, orientation="h",
                     title="Shipments by Departure Port")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("📌 Port codes include the country — a real global sourcing network across multiple continents.")

    # =========================================================================
    # DISTRIBUTION NETWORK
    # =========================================================================
    st.divider()
    st.markdown("### 📍 Where Is Inventory Headed? (Distribution Network)")

    ship_to = sipl_df[sipl_df["ship_to_location"].notna() & (sipl_df["ship_to_location"] != "")]["ship_to_location"]
    top_ship_to = ship_to.value_counts().head(15)
    fig = px.bar(x=top_ship_to.values, y=top_ship_to.index, orientation="h",
                 title="Top 15 Destinations by Shipment Count")
    st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # CONTAINER RESOLUTION
    # =========================================================================
    st.divider()
    st.markdown("### 📦 Container vs. Other Shipping Modes")

    has_container = sipl_df["has_real_container"].sum()
    st.markdown(
        f"""
        **{has_container} of {len(sipl_df)} shipments ({100*has_container/len(sipl_df):.0f}%)**
        are tracked with a real ocean container ID. The rest ship via truck,
        parcel, or another mode that doesn't use a container number — this is
        a normal mix of shipping modes, not a data quality problem.
        """
    )

    # =========================================================================
    # DATA QUALITY
    # =========================================================================
    st.divider()
    st.markdown("### 🔍 Data Quality Notes")

    st.markdown(
        f"""
        - **{original_rows - cleaned_rows} rows removed** during cleaning (no SIPL on file)
        - **{missing_lfd} shipments ({missing_pct:.0f}%)** have no LFD — see the risk callout above
        - **{future_dated} shipment(s)** show a negative age (future-dated initiation)
        - Blank rates are high but expected for several columns (port_eta 76%,
          vessel 74%, departure_port 74%, fr_forwarder 73%) — these move together
          and reflect real domestic/non-ocean shipments, not missing data
        """
    )

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    if st.button("📊 Download Cleaned In-Transit List as CSV", key="in_transit_export_btn"):
        csv = sipl_df.to_csv(index=False)
        st.download_button(
            label="Download CSV File", data=csv,
            file_name=f"in_transit_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", key="in_transit_export_download"
        )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {cleaned_rows:,} shipments tracked")


if __name__ == "__main__":
    st.set_page_config(page_title="In-Transit Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>🚦 IN-TRANSIT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "The operational status board — where every shipment is, right now</p>",
        unsafe_allow_html=True
    )
    render_tab()
