# -*- coding: utf-8 -*-
"""
CONTAINER RISK DASHBOARD
Lean, forward-looking view for the operations team: what's about to become
a problem, and what already is one. Deliberately does not repeat what's
already shown elsewhere (pipeline funnel is in In-Transit Insights,
routing-consistency checks are in Shipment Insights) — this tab is scoped
to catching and resolving risk early, not re-displaying status.

Runs standalone (`streamlit run container_risk_insights.py`) or as a tab
inside logistics_dashboard.py (which imports render_tab()).
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_html_table, clean_in_transit_dataframe, build_sipl_container_rollup

DOC_STAGES = ["SIPL Ready", "Documents Sent", "Need Documents"]
EXCEPTION_STATES = ["On Hold", "Damaged", "On Exam", "Freight Invoice Needed"]


@st.cache_data
def load_rollup():
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    sipl_df, _, _ = clean_in_transit_dataframe(raw)
    rollup = build_sipl_container_rollup(sipl_df)
    return rollup


def _fmt_dates(df):
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    return df


def render_tab():
    """Render the full Container Risk view. Callable standalone or as a tab."""

    rollup = load_rollup()
    today = pd.Timestamp.today().normalize()

    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        Forward-looking risk view across **{len(rollup):,} currently-tracked
        containers** — built to catch problems before they land, not to
        restate what's already shown in the In-Transit or Shipment Insights
        tabs.
        """
    )

    # =========================================================================
    # UPCOMING (7 DAYS) — early warning window
    # =========================================================================
    st.divider()
    st.markdown("### 📅 Upcoming (Next 7 Days)")

    port_7d = rollup[rollup["port_eta"].notna() & (rollup["port_eta"] >= today) & (rollup["port_eta"] <= today + pd.Timedelta(days=7))]
    branch_7d = rollup[rollup["location_eta"].notna() & (rollup["location_eta"] >= today) & (rollup["location_eta"] <= today + pd.Timedelta(days=7))]

    col1, col2 = st.columns(2)
    col1.metric("📍 Reaching Port", len(port_7d))
    col2.metric("🏢 Reaching Branch", len(branch_7d))
    st.caption("A heads-up window — nothing here is a problem yet, but it's what to watch this week.")

    # =========================================================================
    # EXCEPTIONS — needs resolution now
    # =========================================================================
    st.divider()
    st.markdown("### 🚨 Exceptions — Needs Resolution Now")

    exceptions = rollup[rollup["sipl_status"].isin(EXCEPTION_STATES)]
    st.metric("Containers in Exception", len(exceptions))

    if len(exceptions) > 0:
        st.dataframe(
            _fmt_dates(exceptions[["container", "sipl_status", "sipl_count", "port_eta", "sipl_age_days"]]),
            use_container_width=True, hide_index=True
        )
    else:
        st.success("No containers currently on hold, damaged, or under exam.")

    # =========================================================================
    # LFD RISK — catch demurrage before it hits
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 LFD Risk — Delivery Scheduled Past the Deadline")
    st.caption(
        "Different from the LFD check in Invoice Compliance (which flags containers with "
        "no LFD on file). This compares containers that DO have both an LFD and a branch "
        "delivery date on file — if delivery is scheduled after the LFD, storage fees are "
        "on the table."
    )

    has_both = rollup["lfd"].notna() & rollup["location_eta"].notna()
    breach = rollup[has_both & (rollup["location_eta"] > rollup["lfd"])]
    approaching = rollup[
        has_both & (rollup["lfd"] >= today) & (rollup["lfd"] <= today + pd.Timedelta(days=3))
        & (rollup["location_eta"] >= rollup["lfd"])
    ]

    col1, col2 = st.columns(2)
    col1.metric("🔴 Delivery Scheduled Past LFD", len(breach))
    col2.metric("🟡 LFD Within 3 Days, Not Yet Delivered", len(approaching))

    if len(breach) > 0:
        with st.expander(f"See {len(breach)} container(s) with delivery scheduled past LFD"):
            st.dataframe(
                _fmt_dates(breach[["container", "lfd", "location_eta", "sipl_status", "sipl_count"]]),
                use_container_width=True, hide_index=True
            )
    if len(approaching) > 0:
        with st.expander(f"See {len(approaching)} container(s) with LFD approaching"):
            st.dataframe(
                _fmt_dates(approaching[["container", "lfd", "location_eta", "sipl_status", "sipl_count"]]),
                use_container_width=True, hide_index=True
            )
    if len(breach) == 0 and len(approaching) == 0:
        st.success("No delivery-vs-LFD conflicts on file.")

    # =========================================================================
    # DOCUMENTATION RISK — paperwork lagging behind arrival
    # =========================================================================
    st.divider()
    st.markdown("### 📋 Documentation Risk — Paperwork Behind Schedule")
    st.caption(
        "Containers still stuck at an early paperwork stage (Need Documents / SIPL Ready / "
        "Documents Sent) while port arrival is already here or within 7 days — catch this "
        "before the container is sitting at port with nothing cleared."
    )

    doc_risk = rollup[
        rollup["port_eta"].notna()
        & (rollup["port_eta"] <= today + pd.Timedelta(days=7))
        & (rollup["sipl_status"].isin(DOC_STAGES))
    ].copy()
    doc_risk["risk_type"] = "Upcoming"
    doc_risk.loc[doc_risk["port_eta"] < today, "risk_type"] = "Already at Port"

    already = doc_risk[doc_risk["risk_type"] == "Already at Port"]
    upcoming = doc_risk[doc_risk["risk_type"] == "Upcoming"]

    col1, col2 = st.columns(2)
    col1.metric("🔴 Already at Port, Docs Not Ready", len(already))
    col2.metric("🟡 Arriving Soon, Docs Not Ready", len(upcoming))

    if len(doc_risk) > 0:
        st.dataframe(
            _fmt_dates(doc_risk[["container", "sipl_status", "port_eta", "risk_type", "sipl_count"]].sort_values("port_eta")),
            use_container_width=True, hide_index=True
        )
    else:
        st.success("No containers behind on paperwork relative to their port arrival.")

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    if st.button("📊 Download Container Risk Rollup as CSV", key="risk_export_btn"):
        csv = rollup.drop(columns=["sipls"]).to_csv(index=False)
        st.download_button(
            label="Download CSV File", data=csv,
            file_name=f"container_risk_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", key="risk_export_download"
        )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(rollup):,} containers")


if __name__ == "__main__":
    st.set_page_config(page_title="Container Risk", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>🚨 CONTAINER RISK</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "Catch problems before they land</p>",
        unsafe_allow_html=True
    )
    render_tab()
