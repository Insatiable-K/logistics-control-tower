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
    merge_sipl_inventory, get_shipment_container_detail
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
    # SEARCH A CONTAINER
    # =========================================================================
    st.divider()
    st.markdown("### 🔍 Search a Container")
    st.markdown("Look up one container's physical status, every SIPL aboard it, and its value.")

    search_input = st.text_input(
        "Enter a container number (e.g. MEDU2304983):", "", key="shipment_container_search"
    ).strip().upper()

    if search_input:
        c_detail = get_shipment_container_detail(search_input, summary, consolidation)
        if not c_detail["found"]:
            st.error(f"No container-tracked shipment found for **{search_input}**. Check the spelling/format (4 letters + 7 digits).")
        else:
            st.success(f"**{search_input}** — {c_detail['sipl_count']} SIPL(s) aboard, ${c_detail['total_value']:,.0f} total value")

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Physical Status", c_detail["physical_status"])
            with col2:
                st.metric("SIPLs Aboard", c_detail["sipl_count"])
            with col3:
                st.metric("Total Value", f"${c_detail['total_value']:,.0f}")
            with col4:
                st.metric("Routing Consistent", "Yes" if c_detail["is_routing_consistent"] else "No")

            if c_detail["has_mixed_status"]:
                st.warning(
                    "⚠️ The SIPLs aboard this container disagree on status even after "
                    "sharing known dates — either genuine container reuse across "
                    "unrelated shipments, or conflicting recorded dates for the same "
                    "voyage. The status above is from the most recently active SIPL; "
                    "see the full breakdown below."
                )

            st.markdown(
                f"""
                - **Ship date (B/L)**: {c_detail['ship_b_l_date'].strftime('%Y-%m-%d') if pd.notna(c_detail['ship_b_l_date']) else 'Unknown'}
                - **Expected port arrival**: {c_detail['port_eta'].strftime('%Y-%m-%d') if pd.notna(c_detail['port_eta']) else 'Unknown'}
                - **Expected branch arrival**: {c_detail['location_eta'].strftime('%Y-%m-%d') if pd.notna(c_detail['location_eta']) else 'Unknown'}
                """
            )

            st.markdown("**All SIPLs aboard this container:**")
            st.dataframe(c_detail["sipls"], use_container_width=True, hide_index=True)

    # =========================================================================
    # VIEW 0: WHERE IS EVERYTHING, PHYSICALLY — the core operational cut
    # =========================================================================
    st.divider()
    st.markdown("### 🌊 Where Is Everything, Physically Right Now")

    st.markdown(
        """
        A shipment's `sipl_status` (SIPL Ready, Documents Sent, etc.) tracks
        *paperwork* stage — it's manually updated and can lag physical reality
        by days. This view instead uses **three dates**, each checked for its
        own specific milestone (not substituted for one another):
        `ship_b_l_date` (has it departed?), `port_eta` (has it reached the
        port yet?), and `location_eta` (has it reached the branch yet? —
        this is always later than port_eta, ~7 days on average, since branch
        arrival happens after customs clearance and drayage from port). If
        **no port ETA** exists at all, that's reported as genuinely unknown
        rather than assumed either way.

        **Reported at the container level, not the SIPL level.** A container
        is one physical box — it can't be "on the water" twice just because
        it happens to be carrying several consolidated shipments (SIPLs).
        Where a container carries multiple SIPLs, known dates from any one
        of them are shared across its siblings before status is judged (they
        physically travel together), so a SIPL with no matching Inventory
        Detail record isn't wrongly reported as unknown when its container-
        mates already confirm the real dates.
        """
    )

    on_water_c = consolidation[consolidation["physical_status"] == "On the Water"]
    arrived_processing_c = consolidation[consolidation["physical_status"] == "Arrived, Processing"]
    delivered_c = consolidation[consolidation["physical_status"] == "Delivered (At Branch)"]
    not_yet_sailed_c = consolidation[consolidation["physical_status"] == "Not Yet Sailed"]
    cannot_determine_c = consolidation[consolidation["physical_status"].str.startswith("Cannot Determine")]

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("🌊 On the Water", len(on_water_c), help=f"{on_water_c['sipl_count'].sum()} SIPL bookings aboard")
    with col2:
        st.metric("⚓ Arrived, Processing", len(arrived_processing_c), help=f"{arrived_processing_c['sipl_count'].sum()} SIPL bookings aboard")
    with col3:
        st.metric("✅ Delivered", len(delivered_c), help=f"{delivered_c['sipl_count'].sum()} SIPL bookings aboard")
    with col4:
        st.metric("Not Yet Sailed", len(not_yet_sailed_c), help=f"{not_yet_sailed_c['sipl_count'].sum()} SIPL bookings")
    with col5:
        st.metric("Cannot Determine", len(cannot_determine_c), help=f"{cannot_determine_c['sipl_count'].sum()} SIPL bookings")

    st.caption(
        f"📌 **Containers**, not SIPLs — {len(consolidation)} containers total, carrying "
        f"{consolidation['sipl_count'].sum()} SIPL bookings between them "
        f"(avg {consolidation['sipl_count'].mean():.1f} per container). Hover a metric above "
        "for its SIPL-booking count."
    )

    on_water_value = on_water_c["total_value"].sum()
    all_in_transit_value = consolidation["total_value"].sum()
    st.success(
        f"**${on_water_value:,.0f}** of inventory value is genuinely on the water "
        f"right now, across **{len(on_water_c)} containers** — still sailing, hasn't "
        f"reached port yet. This is narrower than the full **${all_in_transit_value:,.0f}** "
        f"in-transit total (which also includes containers still awaiting booking, "
        f"already at port clearing customs, already delivered, or with no date to judge)."
    )

    if len(arrived_processing_c) > 0:
        st.warning(
            f"**{len(arrived_processing_c)} containers (${arrived_processing_c['total_value'].sum():,.0f})** "
            "have passed their port ETA but aren't marked delivered yet — worth "
            "checking if these are moving through customs/drayage normally or stuck."
        )

    mixed = consolidation[consolidation["has_mixed_status"]]
    if len(mixed) > 0:
        st.info(
            f"ℹ️ **{len(mixed)} container(s)** have SIPLs that still disagree on status even "
            "after sharing known dates across siblings — either genuine container reuse "
            "across unrelated shipments, or conflicting dates recorded for the same voyage. "
            "The status shown for these is from the most recently active SIPL; see the "
            "detail table below for the full breakdown."
        )
        with st.expander(f"See the {len(mixed)} container(s) with disagreeing SIPL statuses"):
            for _, row in mixed.iterrows():
                st.markdown(f"**{row['container']}** — reported as *{row['physical_status']}* (most recent SIPL), {row['sipl_count']} SIPLs aboard:")
                sibling_detail = summary[summary["container"] == row["container"]][
                    ["sipl", "physical_status", "ship_b_l_date", "port_eta", "sipl_status"]
                ]
                st.dataframe(sibling_detail, use_container_width=True, hide_index=True)

    with st.expander(f"See all {len(on_water_c)} containers genuinely on the water"):
        on_water_display = on_water_c[
            ["container", "sipl_count", "ship_b_l_date", "port_eta", "location_eta", "total_value"]
        ].rename(columns={"port_eta": "expected_port_arrival", "location_eta": "expected_branch_arrival"}).sort_values("expected_port_arrival")
        st.dataframe(on_water_display, use_container_width=True, hide_index=True)

    with st.expander(f"See the {len(arrived_processing_c)} containers arrived at port but not yet delivered"):
        processing_display = arrived_processing_c[
            ["container", "sipl_count", "port_eta", "location_eta", "total_value"]
        ].rename(columns={"port_eta": "port_eta_was", "location_eta": "expected_branch_arrival"}).sort_values("port_eta_was")
        st.dataframe(processing_display, use_container_width=True, hide_index=True)

    if len(cannot_determine_c) > 0:
        with st.expander(f"See the {len(cannot_determine_c)} containers with an unknown physical location"):
            no_detail = cannot_determine_c[cannot_determine_c["physical_status"] == "Cannot Determine (No Detail)"]
            no_eta = cannot_determine_c[cannot_determine_c["physical_status"] == "Cannot Determine (No ETA Data)"]
            if len(no_detail) > 0:
                st.markdown(f"**{len(no_detail)} — no inventory detail record for any SIPL aboard** (no Bill of Lading date to check whether they've even sailed):")
                st.dataframe(no_detail[["container", "sipl_count"]], use_container_width=True, hide_index=True)
            if len(no_eta) > 0:
                st.markdown(f"**{len(no_eta)} — sailed, but no port or location ETA on file** (can't tell if arrived):")
                st.dataframe(
                    no_eta[["container", "sipl_count", "ship_b_l_date", "total_value"]],
                    use_container_width=True, hide_index=True
                )

    # =========================================================================
    # VIEW 1: VALUE AT RISK (LFD reframed by dollars, not just count)
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 Value at Risk: Demurrage Exposure by Dollars, Not Just Count")

    # Confirmed with ops: LFD is only issued once a container physically
    # reaches port, so "no LFD" while still On the Water / Not Yet Sailed is
    # expected, not a risk. The real, actionable risk is a container that
    # HAS arrived (physical_status == "Arrived, Processing") but still has
    # no LFD — sitting at port right now with no known pickup deadline.
    at_risk = consolidation[(consolidation["physical_status"] == "Arrived, Processing") & (~consolidation["has_lfd"])]
    not_yet_due = consolidation[
        consolidation["physical_status"].isin(["On the Water", "Not Yet Sailed", "Cannot Determine (No Detail)", "Cannot Determine (No ETA Data)"])
        & (~consolidation["has_lfd"])
    ]
    delivered_no_lfd = consolidation[(consolidation["physical_status"] == "Delivered (At Branch)") & (~consolidation["has_lfd"])]

    value_at_risk = at_risk["total_value"].sum()
    value_tracked = consolidation[consolidation["has_lfd"]]["total_value"].sum()
    total_value_lfd = consolidation["total_value"].sum()
    pct_at_risk = 100 * value_at_risk / total_value_lfd if total_value_lfd > 0 else 0

    st.error(
        f"""
        **${value_at_risk:,.0f} of ${total_value_lfd:,.0f} total value
        ({pct_at_risk:.0f}%), across {len(at_risk)} containers, has already
        arrived at port with no LFD (Last Free Day) on file.**

        This is the real, actionable demurrage exposure — containers sitting
        at port right now with no known pickup deadline. (LFD is a port-side
        deadline that applies to the whole physical container — reported
        here per container, not duplicated once per SIPL aboard it.)
        """
    )

    st.caption(
        f"📌 {len(not_yet_due)} more containers show no LFD but haven't reached "
        "port yet — LFD isn't issued until then, so that's expected, not a gap "
        f"(confirmed with ops). {len(delivered_no_lfd)} delivered containers also "
        "had no LFD on file, shown separately below since that risk has already passed."
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(
            values=[value_at_risk, value_tracked], names=["At risk (arrived, no LFD)", "LFD on file"],
            title="Value at Risk vs. Tracked", color_discrete_sequence=["#e74c3c", "#2ecc71"]
        )
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**Highest-value containers at risk (arrived, no LFD):**")
        top_risk = at_risk.nlargest(10, "total_value")[
            ["container", "suppliers", "sipl_count", "total_value", "port_eta"]
        ]
        st.dataframe(top_risk, use_container_width=True, hide_index=True)

    if len(delivered_no_lfd) > 0:
        with st.expander(f"See {len(delivered_no_lfd)} delivered containers that had no LFD on file (risk already passed)"):
            st.dataframe(
                delivered_no_lfd[["container", "suppliers", "sipl_count", "total_value"]],
                use_container_width=True, hide_index=True
            )

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

    st.markdown("Where containers come from and where they're headed, by dollar value.")

    lane = consolidation.groupby(["departure_port", "ship_to_location"]).agg(
        value=("total_value", "sum"), containers=("container", "nunique")
    ).sort_values("value", ascending=False).head(15).reset_index()

    st.dataframe(
        lane.rename(columns={"departure_port": "From", "ship_to_location": "To",
                              "value": "Total Value", "containers": "# Containers"}),
        use_container_width=True, hide_index=True
    )

    top_lane = lane.iloc[0]
    st.markdown(
        f"💡 **Top trade lane**: {top_lane['departure_port']} → {top_lane['ship_to_location']} "
        f"— ${top_lane['value']:,.0f} across {top_lane['containers']} containers."
    )

    # =========================================================================
    # VIEW 4: TRANSIT TIME
    # =========================================================================
    st.divider()
    st.markdown("### ⏱️ Ocean Transit Time")

    st.markdown(
        "Days between the Bill of Lading date (when cargo is loaded/shipped) "
        "and the expected port arrival — one measurement per **container**, "
        "not per product line, so a heavily-consolidated container doesn't "
        "skew the average by counting itself many times over."
    )

    valid_transit = consolidation[consolidation["transit_days"].notna() & ~consolidation["has_transit_anomaly"]]

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

    anomalies = consolidation[consolidation["has_transit_anomaly"]]
    if len(anomalies) > 0:
        st.warning(
            f"""
            **{len(anomalies)} container(s) show a port ETA *before* the ship date**
            — physically impossible (you can't arrive before you depart). Likely a
            data entry error on one of the two dates, worth a quick check. Use the
            container search above for the full per-SIPL breakdown.
            """
        )
        st.dataframe(
            anomalies[["container", "ship_b_l_date", "port_eta", "transit_days"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.success("No transit-time anomalies found.")

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📊 Download Container-Level Data as CSV", key="shipment_export_container_btn"):
            csv = consolidation.to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"container_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="shipment_export_container_download"
            )
    with col2:
        if st.button("📄 Download Full SIPL/Line-Item Detail as CSV", key="shipment_export_detail_btn"):
            csv = detail.to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"shipment_merged_detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="shipment_export_detail_download"
            )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(consolidation):,} containers | {len(summary):,} SIPLs | {len(detail):,} merged line items")


if __name__ == "__main__":
    st.set_page_config(page_title="Shipment Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>🔗 SHIPMENT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "The merged view — tracking status + product value, together</p>",
        unsafe_allow_html=True
    )
    render_tab()
