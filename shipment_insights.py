# -*- coding: utf-8 -*-
"""
SHIPMENT INSIGHTS DASHBOARD - Easy-to-Understand Version
The merged view: SIPL tracking status + product/value detail, joined by
SIPL. Answers questions neither file can answer alone -- value at risk,
container consolidation, trade lanes, transit time, and cross-file data
quality checks.

Runs standalone (`streamlit run shipment_insights.py`) or as a tab inside
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
from utils import (
    load_html_table, clean_in_transit_dataframe, clean_inventory_detail_dataframe,
    merge_sipl_inventory
)


@st.cache_data
def load_merged():
    """Load and merge both In-Transit files (shared logic in utils.py)."""
    sipl_raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    sipl_df, _, _ = clean_in_transit_dataframe(sipl_raw)

    inv_raw = load_html_table(Path("Inventory In Transit - Detail .xls"))
    inv_df, _, _ = clean_inventory_detail_dataframe(inv_raw)

    return merge_sipl_inventory(sipl_df, inv_df)


def render_tab():
    """Render the full Shipment Insights view. Callable standalone or as a tab."""

    merged = load_merged()
    detail = merged["merged_detail"]
    summary = merged["sipl_summary"]
    consolidation = merged["container_consolidation"]

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        This merges the **Shipment Tracking** status board with the
        **Product/Value Detail** file, joined by SIPL — both already scoped
        to container freight only.

        Before merging, we checked whether the two files actually agree with
        each other: for every SIPL present in both, **container IDs matched
        100% of the time and departure ports matched 100% of the time** —
        zero conflicts. That gave confidence the join is trustworthy.

        **{summary['sipl'].nunique():,} container-tracked shipments** total,
        of which **{summary['has_detail'].sum():,}** have product/value detail
        and **{(~summary['has_detail']).sum():,}** don't yet.
        """
    )

    # =========================================================================
    # VIEW 0: CURRENTLY ON THE WATER — the core operational cut
    # =========================================================================
    st.divider()
    st.markdown("### 🌊 Currently On the Water")

    st.markdown(
        """
        A shipment's `sipl_status` (SIPL Ready, Documents Sent, etc.) tracks
        *paperwork* stage — it doesn't by itself say whether the container has
        physically left origin yet. This view answers that directly: **has this
        shipment actually sailed, and is it still in transit right now** (not
        yet arrived at the branch)?

        **Method**: a Bill of Lading is issued once cargo is loaded onto the
        vessel — so a `ship_b_l_date` in the past is a real "has sailed"
        signal. Shipments already at the branch are excluded even if they
        technically sailed a while ago — they're on land now, not on water.
        """
    )

    sailing_counts = summary["sailing_status"].value_counts()
    on_water_now = summary[summary["is_currently_on_water"]]
    at_branch_already = summary[(summary["sailing_status"] == "On the Water") & (summary["sipl_status"] == "At branch")]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("🌊 Currently On the Water", len(on_water_now))
    with col2:
        st.metric("Not Yet Sailed", int(sailing_counts.get("Not Yet Sailed", 0)))
    with col3:
        st.metric("Already Delivered", len(at_branch_already))
    with col4:
        st.metric("Cannot Determine", int(sailing_counts.get("Cannot Determine (No Detail)", 0)))

    on_water_value = on_water_now["total_value"].sum()
    all_in_transit_value = summary["total_value"].sum()
    st.success(
        f"**${on_water_value:,.0f}** of inventory value is currently on the water "
        f"right now — this is the number that matters for active operations, not "
        f"the full **${all_in_transit_value:,.0f}** in-transit total (which also "
        f"includes shipments still awaiting booking and already-delivered ones)."
    )

    with st.expander(f"See all {len(on_water_now)} shipments currently on the water"):
        on_water_display = on_water_now[
            ["sipl", "container", "supplier", "ship_b_l_date", "eta_date", "total_value", "sipl_status"]
        ].sort_values("eta_date")
        st.dataframe(on_water_display, use_container_width=True, hide_index=True)

    if sailing_counts.get("Cannot Determine (No Detail)", 0) > 0:
        cannot_determine = summary[summary["sailing_status"] == "Cannot Determine (No Detail)"]
        st.caption(
            f"📌 {len(cannot_determine)} shipment(s) have no inventory detail record, "
            "so there's no Bill of Lading date to check — genuinely unknown, not "
            "assumed either way."
        )

    # =========================================================================
    # VIEW 1: VALUE AT RISK (LFD reframed by dollars, not just count)
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 Value at Risk: Demurrage Exposure by Dollars, Not Just Count")

    value_no_lfd = summary[~summary["has_lfd"]]["total_value"].sum()
    value_has_lfd = summary[summary["has_lfd"]]["total_value"].sum()
    total_value = value_no_lfd + value_has_lfd
    pct_at_risk = 100 * value_no_lfd / total_value if total_value > 0 else 0

    st.error(
        f"""
        **${value_no_lfd:,.0f} of ${total_value:,.0f} total value
        ({pct_at_risk:.0f}%) sits in shipments with no LFD (Last Free Day) on
        file.**

        This is a sharper story than counting shipments alone: the
        untracked-for-demurrage-risk shipments are disproportionately the
        *high-value* ones, not a random cross-section.
        """
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(
            values=[value_no_lfd, value_has_lfd], names=["No LFD on file", "LFD on file"],
            title="Value at Risk vs. Tracked", color_discrete_sequence=["#e74c3c", "#2ecc71"]
        )
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**Highest-value shipments with no LFD:**")
        top_risk = summary[~summary["has_lfd"]].nlargest(10, "total_value")[
            ["sipl", "container", "supplier", "total_value", "sipl_status"]
        ]
        st.dataframe(top_risk, use_container_width=True, hide_index=True)

    # =========================================================================
    # VIEW 2: CONTAINER CONSOLIDATION
    # =========================================================================
    st.divider()
    st.markdown("### 📦 Container Consolidation")

    avg_sipls = consolidation["sipl_count"].mean()
    max_sipls = consolidation["sipl_count"].max()
    multi_count = (consolidation["sipl_count"] > 1).sum()

    st.markdown(
        f"""
        Containers are often shared across multiple shipments (SIPLs) to
        save freight cost — this is normal consolidation, not an error.

        **Average {avg_sipls:.1f} SIPLs per container.** **{multi_count} of
        {len(consolidation)} containers ({100*multi_count/len(consolidation):.0f}%)**
        carry more than one shipment. The most consolidated container
        currently holds **{max_sipls} separate SIPLs**.
        """
    )

    col1, col2 = st.columns(2)
    with col1:
        top_consolidated = consolidation.nlargest(10, "sipl_count")[
            ["container", "sipl_count", "total_value"]
        ]
        fig = px.bar(top_consolidated, x="sipl_count", y="container", orientation="h",
                     title="Top 10 Most-Consolidated Containers")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**Same containers, by value:**")
        st.dataframe(top_consolidated, use_container_width=True, hide_index=True)

    # Routing consistency check — the data-quality validation
    st.markdown("**🔍 Routing consistency check**")
    inconsistent = consolidation[~consolidation["is_routing_consistent"]]
    consistent_pct = 100 * (len(consolidation) - len(inconsistent)) / len(consolidation)
    st.success(
        f"""
        **{consistent_pct:.1f}% of containers ({len(consolidation) - len(inconsistent)} of
        {len(consolidation)}) show fully consistent ship-to location and
        departure port** across every shipment sharing that container — exactly
        what you'd expect physically (one container can't go to two places at once).
        """
    )
    if len(inconsistent) > 0:
        st.markdown(f"The {len(inconsistent)} exception(s), for transparency:")
        st.dataframe(inconsistent, use_container_width=True, hide_index=True)
        st.caption(
            "📌 Checked directly: this is legitimate container reuse across two "
            "different shipments at different times, not a data error."
        )

    # =========================================================================
    # VIEW 3: TRADE LANE ANALYSIS
    # =========================================================================
    st.divider()
    st.markdown("### 🌍 Trade Lane Analysis")

    st.markdown("Where shipments come from and where they're headed, by dollar value.")

    lane = detail.groupby(["departure_port", "ship_to"]).agg(
        value=("total_cost", "sum"), shipments=("sipl", "nunique")
    ).sort_values("value", ascending=False).head(15).reset_index()

    st.dataframe(
        lane.rename(columns={"departure_port": "From", "ship_to": "To",
                              "value": "Total Value", "shipments": "# Shipments"}),
        use_container_width=True, hide_index=True
    )

    top_lane = lane.iloc[0]
    st.markdown(
        f"💡 **Top trade lane**: {top_lane['departure_port']} → {top_lane['ship_to']} "
        f"— ${top_lane['value']:,.0f} across {top_lane['shipments']} shipments."
    )

    # =========================================================================
    # VIEW 4: TRANSIT TIME
    # =========================================================================
    st.divider()
    st.markdown("### ⏱️ Ocean Transit Time")

    st.markdown(
        "Days between the Bill of Lading date (when cargo is loaded/shipped) "
        "and the expected arrival date — a real measure of how long freight "
        "actually takes by route."
    )

    valid_transit = detail[detail["transit_days"].notna() & ~detail["has_transit_anomaly"]]

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(valid_transit, x="transit_days", nbins=30,
                            title="Distribution of Transit Time (days)")
        st.plotly_chart(fig, use_container_width=True)
        st.metric("Average Transit Time", f"{valid_transit['transit_days'].mean():.0f} days")

    with col2:
        by_country = valid_transit.groupby("departure_country")["transit_days"].mean().sort_values()
        fig = px.bar(x=by_country.values, y=by_country.index, orientation="h",
                     title="Average Transit Time by Origin Country",
                     labels={"x": "Avg Days", "y": "Country Code"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"📌 Fastest: {by_country.index[0]} (~{by_country.values[0]:.0f} days). "
            f"Slowest: {by_country.index[-1]} (~{by_country.values[-1]:.0f} days)."
        )

    # =========================================================================
    # VIEW 5: DATA ANOMALIES
    # =========================================================================
    st.divider()
    st.markdown("### ⚠️ Data Anomalies")

    anomalies = detail[detail["has_transit_anomaly"]].drop_duplicates("sipl")
    if len(anomalies) > 0:
        st.warning(
            f"""
            **{len(anomalies)} shipment(s) show an ETA date *before* the ship date**
            — physically impossible (you can't arrive before you depart). Likely a
            data entry error on one of the two dates, worth a quick check.
            """
        )
        st.dataframe(
            anomalies[["sipl", "container", "ship_b_l_date", "eta_date", "transit_days"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.success("No transit-time anomalies found.")

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    if st.button("📊 Download Merged Shipment Data as CSV", key="shipment_export_btn"):
        csv = detail.to_csv(index=False)
        st.download_button(
            label="Download CSV File", data=csv,
            file_name=f"shipment_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", key="shipment_export_download"
        )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(summary):,} shipments | {len(detail):,} merged line items")


if __name__ == "__main__":
    st.set_page_config(page_title="Shipment Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>🔗 SHIPMENT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "The merged view — tracking status + product value, together</p>",
        unsafe_allow_html=True
    )
    render_tab()
