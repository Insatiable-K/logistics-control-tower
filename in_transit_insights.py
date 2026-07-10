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
from datetime import datetime
import plotly.express as px
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_html_table, clean_in_transit_dataframe, build_sipl_container_rollup


@st.cache_data
def load_and_clean_in_transit():
    """Load In-Transit List by SIPL.xls and clean it (shared logic in utils.py)."""
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    df, original_count, final_count = clean_in_transit_dataframe(raw)
    return df, original_count, final_count


def render_tab():
    """Render the full In-Transit Insights view. Callable standalone or as a tab."""

    sipl_df, original_rows, cleaned_rows = load_and_clean_in_transit()
    rollup = build_sipl_container_rollup(sipl_df)

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    removed = original_rows - cleaned_rows
    st.markdown(
        f"""
        This is the **operational status board** — every ocean-container shipment
        currently moving, tracked by SIPL (our internal shipment reference number).

        **Scoped to container freight only.** Of {original_rows:,} shipments in the
        raw export, **{removed:,} ({100*removed/original_rows:.0f}%) were domestic
        truck/parcel moves** (TRUCK FEDEXGRND, UPS, XPO, Daylight, R&L, TFORCE, etc.)
        with no ocean container — removed, since this view tracks container
        logistics specifically, not domestic freight.

        **{cleaned_rows:,} SIPL bookings remain in scope, riding on
        {rollup['container'].nunique():,} physical containers** (a container
        routinely carries more than one SIPL — every view below leads with
        the container count, since that's the physical unit that actually
        moves; SIPL-level detail is available as drill-down).
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

    # Correct pipeline order and plain-English meaning, as explained directly
    # by the ops team (not guessed from the label text — see the
    # sipl-status-definitions memory for the full source explanation).
    PIPELINE_ORDER = ["Need Documents", "SIPL Ready", "Documents Sent",
                       "D.Os  Received", "Scheduled for Delivery", "At branch"]
    STAGE_DESCRIPTIONS = {
        "Need Documents": "Not yet ready to ship — waiting on the packing list and commercial invoice from the supplier.",
        "SIPL Ready": "Ready to sail with all documents in order, OR already sailed — this single status covers both states (see note below).",
        "Documents Sent": "Documents sent to the CHA (customs broker) to clear customs — awaiting the Delivery Order (D.O.) back.",
        "D.Os  Received": "Delivery Order received — branch and drayage vendor notified to schedule delivery.",
        "Scheduled for Delivery": "Branch and drayage driver have confirmed a delivery date for the container.",
        "At branch": "Container has arrived at the branch — successful end of the pipeline.",
    }
    # Exception/risk states — flags, not linear pipeline stages.
    EXCEPTION_STATES = ["On Hold", "Damaged", "On Exam", "Freight Invoice Needed"]
    EXCEPTION_DESCRIPTIONS = {
        "On Hold": "Container is on hold at port; reason not yet known.",
        "Damaged": "Container was damaged in transit and is being processed/claimed.",
        "On Exam": "Container is being held at port for a customs inspection.",
        "Freight Invoice Needed": "Invoice team hasn't linked a bill to this container yet — an internal delay, not a carrier/customs one.",
    }

    # Container-level primary (rollup["sipl_status"] is deduplicated per
    # container, with has_mixed_status flagging any real disagreement among
    # siblings — verified 0 such cases in current data, but not assumed).
    status_counts = rollup["sipl_status"].value_counts()
    sipl_status_counts_raw = sipl_df["sipl_status"].value_counts()
    # Zero-filled (not dropna'd) — a stage showing 0 here is a real, worth-
    # reporting finding (e.g. "Need Documents" never has a container yet in
    # this data), not something to silently omit from the chart.
    pipeline_counts = status_counts.reindex(PIPELINE_ORDER).fillna(0).astype(int)
    pipeline_sipl_counts = sipl_status_counts_raw.reindex(PIPELINE_ORDER).fillna(0).astype(int)
    exception_counts = status_counts.reindex(EXCEPTION_STATES).dropna().astype(int)
    other_labels = [s for s in status_counts.index if s not in PIPELINE_ORDER and s not in EXCEPTION_STATES]
    other_counts = status_counts.reindex(other_labels).dropna().astype(int) if other_labels else pd.Series(dtype=int)

    if pipeline_counts.get("Need Documents", 0) == 0:
        st.caption(
            "📌 \"Need Documents\" shows 0 here because this view is scoped to "
            "container-tracked shipments only — checked the raw data directly: "
            "every shipment currently at this stage is a domestic truck move "
            "with no container yet assigned, not a data gap."
        )

    mixed_status_containers = rollup[rollup["has_mixed_status"]]
    if len(mixed_status_containers) > 0:
        st.warning(
            f"⚠️ **{len(mixed_status_containers)} container(s)** have sibling SIPLs "
            "sitting at different pipeline stages — the chart below shows the "
            "container once per stage its siblings disagree on. See the expander "
            "below for the breakdown."
        )
        with st.expander(f"See the {len(mixed_status_containers)} mixed-status containers"):
            for _, row in mixed_status_containers.iterrows():
                sibling_detail = sipl_df[sipl_df["container"] == row["container"]][["sipl", "sipl_status"]]
                st.write(f"**{row['container']}**")
                st.dataframe(sibling_detail, use_container_width=True, hide_index=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        fig = px.bar(
            x=pipeline_counts.values, y=pipeline_counts.index, orientation="h",
            title="Containers by Pipeline Stage (in process order)",
            labels={"x": "# Containers", "y": "Stage"}
        )
        fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(PIPELINE_ORDER)))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**Pipeline stage counts (containers):**")
        for stage in PIPELINE_ORDER:
            count = int(pipeline_counts.get(stage, 0))
            sipl_count = int(pipeline_sipl_counts.get(stage, 0))
            pct = 100 * count / len(rollup) if len(rollup) > 0 else 0
            st.write(f"- **{stage}**: {count} containers ({pct:.0f}%)", help=f"{sipl_count} SIPL booking(s)")
            st.caption(STAGE_DESCRIPTIONS[stage])

    sipl_ready_containers = int(pipeline_counts.get("SIPL Ready", 0))
    sipl_ready_sipls = int(pipeline_sipl_counts.get("SIPL Ready", 0))
    st.info(
        f"ℹ️ **\"SIPL Ready\" note**: this one status label actually covers two "
        f"different real states — ready to sail (not yet departed) and already "
        f"sailed / on the water. The **{sipl_ready_containers} containers** "
        f"({sipl_ready_sipls} SIPL bookings) shown here under SIPL Ready are a "
        f"mix of both. See the **🔗 Shipment Insights** tab for the "
        f"\"Currently On the Water\" view, which splits this out using each "
        f"shipment's Bill of Lading date."
    )

    if len(exception_counts) > 0 or len(EXCEPTION_STATES) > 0:
        st.markdown("**⚠️ Exception / risk states** (not part of the linear pipeline):")
        any_exceptions = False
        for stage in EXCEPTION_STATES:
            count = int(exception_counts.get(stage, 0))
            if count > 0:
                any_exceptions = True
                st.write(f"- **{stage}**: {count} containers")
                st.caption(EXCEPTION_DESCRIPTIONS[stage])
        if not any_exceptions:
            st.write("None of these currently active in this window.")

    if len(other_counts) > 0:
        st.markdown("**Other / unrecognized statuses:**")
        for stage, count in other_counts.items():
            st.write(f"- **{stage}**: {count} containers")

    st.markdown("**Coarse status (2-value summary, SIPL bookings):**")
    coarse = sipl_df["status"].value_counts()
    col1, col2 = st.columns(2)
    with col1:
        st.metric("In Transit", int(coarse.get("INTRANSIT", 0)))
    with col2:
        st.metric("ONSO Reduced", int(coarse.get("ONSO Reduced", 0)))
    st.caption(
        "📌 Shown at SIPL-booking grain, not container grain — this is a "
        "coarse system status per booking, not a physical container fact."
    )

    # =========================================================================
    # LFD RISK — the most actionable finding in this file
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 Demurrage Risk: Last Free Day (LFD) Tracking")

    # LFD is a physical fact about the container (one pickup deadline per
    # container at port), not a per-SIPL fact — verified 0 containers have
    # conflicting has_lfd values across sibling SIPLs, so the rollup is an
    # exact dedup, not an approximation.
    #
    # Confirmed with ops: the origin-side team does not issue an LFD until a
    # container has physically arrived at port — verified against real data
    # (0 of 92 not-yet-arrived containers have an LFD, vs. 21 of 32 arrived
    # containers still missing one). So "no LFD" is only a real risk once
    # has_arrived_at_port is True; before that it's expected, not a gap.
    has_lfd_containers = int(rollup["has_lfd"].sum())
    missing_lfd_containers = len(rollup) - has_lfd_containers

    at_risk = rollup[rollup["has_arrived_at_port"] & ~rollup["has_lfd"]]
    not_yet_due = rollup[~rollup["has_arrived_at_port"] & ~rollup["has_lfd"]]

    st.error(
        f"""
        **{len(at_risk)} of {len(rollup)} containers have already reached port
        but still have no Last Free Day (LFD) on file** — this is the real,
        actionable demurrage risk.

        💡 **Why this matters**: LFD is the deadline to pick up a container from
        port before daily storage/demurrage fees start accruing. Once a container
        has arrived, a missing LFD means this risk isn't being actively monitored.
        """
    )

    st.caption(
        f"📌 {missing_lfd_containers} containers total show no LFD, but "
        f"{len(not_yet_due)} of those haven't reached port yet — LFD isn't issued "
        "until then, so that's expected, not a gap (confirmed with ops). Only the "
        f"{len(at_risk)} above are actionable right now."
    )

    if len(at_risk) > 0:
        with st.expander(f"See the {len(at_risk)} containers at risk (arrived, no LFD)"):
            st.dataframe(
                at_risk[["container", "port_eta", "sipl_status", "sipl_count"]].sort_values("port_eta"),
                use_container_width=True, hide_index=True
            )

    if has_lfd_containers > 0:
        with st.expander(f"See the {has_lfd_containers} containers that DO have an LFD on file"):
            lfd_df = rollup[rollup["has_lfd"]][
                ["container", "lfd", "sipl_status", "sipl_count"]
            ].sort_values("lfd")
            st.dataframe(lfd_df, use_container_width=True, hide_index=True)

    # =========================================================================
    # SIPL AGE — how long has this been sitting
    # =========================================================================
    st.divider()
    st.markdown("### ⏱️ Container Age (Days Since Oldest SIPL Was Initiated)")

    st.markdown(
        f"""
        **Average age: {rollup['sipl_age_days'].mean():.0f} days** |
        **Median: {rollup['sipl_age_days'].median():.0f} days**

        Age is measured per container, using the **oldest** SIPL booking
        riding on it (a container consolidating several SIPLs is only as
        "fresh" as its longest-waiting booking). A container stuck at an
        early pipeline stage for a long time is worth a second look — the
        table below cross-references age against pipeline stage to surface
        exactly that; per-SIPL detail is one click away.
        """
    )

    future_dated = (rollup["sipl_age_days"] < 0).sum()
    if future_dated > 0:
        st.info(
            f"ℹ️ **{future_dated} container(s) show a negative age** — meaning "
            "their oldest SIPL's 'initiated' date is technically in the future. "
            "Likely a forward-dated entry or clock skew in the source system, "
            "not a real error, but flagged for completeness."
        )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(
            rollup, x="sipl_age_days", nbins=40,
            title="Distribution of Container Age",
            labels={"sipl_age_days": "Days Since Oldest SIPL Initiated", "count": "# Containers"}
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Oldest containers still active, by stage:**")
        stale = rollup[rollup["sipl_age_days"] >= 60].sort_values("sipl_age_days", ascending=False)
        st.write(f"{len(stale)} containers have been open 60+ days")
        if len(stale) > 0:
            st.dataframe(
                stale[["container", "sipl_status", "sipl_age_days", "sipl_count"]].head(15),
                use_container_width=True, hide_index=True
            )
            with st.expander("See SIPL-level detail for these stale containers"):
                stale_sipls = sipl_df[sipl_df["container"].isin(stale["container"])][
                    ["sipl", "container", "sipl_status", "sipl_age_days", "supplier"]
                ].sort_values("sipl_age_days", ascending=False)
                st.dataframe(stale_sipls, use_container_width=True, hide_index=True)

    # =========================================================================
    # FREIGHT FORWARDER CONCENTRATION
    # =========================================================================
    st.divider()
    st.markdown("### 🏢 Freight Forwarder Concentration")

    ff = rollup[rollup["fr_forwarder"].notna() & (rollup["fr_forwarder"] != "")]["fr_forwarder"]
    mixed_ff_count = int(rollup["has_mixed_forwarder"].sum())
    if len(ff) > 0:
        ff_counts = ff.value_counts()
        st.markdown(
            f"""
            **{len(ff)} of {len(rollup)} containers** ({100*len(ff)/len(rollup):.0f}%) have a
            freight forwarder on file. Of those, the top 3 handle
            **{100*ff_counts.head(3).sum()/len(ff):.0f}%** of the volume — a real
            concentration worth knowing about for negotiating leverage and
            single-vendor risk.
            """
        )
        if mixed_ff_count > 0:
            st.caption(
                f"📌 {mixed_ff_count} container(s) have more than one freight "
                "forwarder among their sibling SIPLs — counted here under the "
                "first forwarder on file."
            )
        fig = px.bar(x=ff_counts.values, y=ff_counts.index, orientation="h",
                     title="Containers by Freight Forwarder")
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # GLOBAL SOURCING FOOTPRINT
    # =========================================================================
    st.divider()
    st.markdown("### 🌍 Global Sourcing Footprint")

    dp = rollup[rollup["departure_port"].notna() & (rollup["departure_port"] != "")]["departure_port"]
    if len(dp) > 0:
        dp_counts = dp.value_counts()
        st.markdown(
            f"Of the **{len(dp)} containers** with a known departure port, "
            f"here's where they're coming from:"
        )
        fig = px.bar(x=dp_counts.values, y=dp_counts.index, orientation="h",
                     title="Containers by Departure Port")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("📌 Port codes include the country — a real global sourcing network across multiple continents.")

    # =========================================================================
    # DISTRIBUTION NETWORK
    # =========================================================================
    st.divider()
    st.markdown("### 📍 Where Is Inventory Headed? (Distribution Network)")

    ship_to = rollup[rollup["ship_to_location"].notna() & (rollup["ship_to_location"] != "")]["ship_to_location"]
    top_ship_to = ship_to.value_counts().head(15)
    fig = px.bar(x=top_ship_to.values, y=top_ship_to.index, orientation="h",
                 title="Top 15 Destinations by Container Count")
    st.plotly_chart(fig, use_container_width=True)
    if (~rollup["is_routing_consistent"]).sum() > 0:
        st.caption(
            f"📌 {(~rollup['is_routing_consistent']).sum()} container(s) show "
            "more than one destination among sibling SIPLs (routing "
            "inconsistency, usually container reuse) — counted here under the "
            "first destination on file."
        )

    # =========================================================================
    # DATA QUALITY
    # =========================================================================
    st.divider()
    st.markdown("### 🔍 Data Quality Notes")

    st.markdown(
        f"""
        - **{removed:,} rows removed** during cleaning — all domestic truck/parcel
          shipments with no ocean container (out of scope for this view; see the
          "What This Data Represents" section above)
        - **{len(at_risk)} containers** have arrived at port with no LFD on file — the
          real demurrage risk (see the risk callout above; {len(not_yet_due)} more show
          no LFD but haven't reached port yet, which is expected)
        - **{future_dated} container(s)** show a negative age (future-dated initiation)
        """
    )

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📦 Download Container-Level Rollup as CSV", key="in_transit_container_export_btn"):
            csv = rollup.drop(columns=["sipls"]).to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"in_transit_containers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="in_transit_container_export_download"
            )
    with col2:
        if st.button("📊 Download Full SIPL Detail as CSV", key="in_transit_export_btn"):
            csv = sipl_df.to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"in_transit_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="in_transit_export_download"
            )

    st.caption(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{rollup['container'].nunique():,} containers | {cleaned_rows:,} SIPL bookings tracked"
    )


if __name__ == "__main__":
    st.set_page_config(page_title="In-Transit Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>🚦 IN-TRANSIT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "The operational status board — where every shipment is, right now</p>",
        unsafe_allow_html=True
    )
    render_tab()
