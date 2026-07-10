# -*- coding: utf-8 -*-
"""
INVOICE COMPLIANCE DASHBOARD
For every currently-tracked container, per required cost category (Ocean
Freight, Customs, Duty, Drayage): is the invoice Complete (GL-confirmed),
Pending (something's already in motion, chase the vendor), or Missing
(nothing on file at all, create a placeholder and chase the vendor from
scratch).

Runs standalone (`streamlit run invoice_compliance_insights.py`) or as a
tab inside logistics_dashboard.py (which imports render_tab()).
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.express as px
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, clean_in_transit_dataframe, clean_bills_dataframe,
    clean_gl_dataframe, score_container_invoice_compliance, REQUIRED_CATEGORIES,
    build_sipl_container_rollup
)

CATEGORY_LABELS = {"OF": "Ocean Freight", "CUSTOMS": "Customs", "DUTY": "Duty", "DRAYAGE": "Drayage"}


@st.cache_data
def load_tracked_containers():
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    df, _, _ = clean_in_transit_dataframe(raw)
    return df


@st.cache_data
def load_bills():
    bills_raw = load_html_table(Path("Bills.xls"))
    bills, _, _ = clean_bills_dataframe(bills_raw)
    return bills


@st.cache_data
def load_gl():
    gl_1275_raw = load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls"))
    gl_1313_raw = load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls"))
    return clean_gl_dataframe(gl_1275_raw, gl_1313_raw)


@st.cache_data
def load_compliance():
    sipl_df = load_tracked_containers()
    bills = load_bills()
    gl = load_gl()
    tracked_containers = sorted(sipl_df["container"].dropna().unique())
    result = score_container_invoice_compliance(bills, gl, tracked_containers)
    return result, sipl_df


def render_tab():
    """Render the full Invoice Compliance view. Callable standalone or as a tab."""

    result, sipl_df = load_compliance()
    detail = result["compliance_detail"]
    pending_bills = result["pending_bills"]
    missing_summary = result["missing_summary"]
    tracked_containers = sorted(sipl_df["container"].dropna().unique())

    # Container -> supplier lookup, for a starting-point contact even on
    # containers with zero bills on file (the product supplier isn't the
    # same as the freight vendor, but it's a real lead when there's nothing
    # else to go on).
    container_supplier = sipl_df.groupby("container")["supplier"].first()

    # Arrival urgency, reusing the same container rollup as Container Risk —
    # a missing bill on a container still weeks from sailing isn't urgent;
    # one on a container already at port is.
    rollup = build_sipl_container_rollup(sipl_df)
    today = pd.Timestamp.today().normalize()

    def _arrival_bucket(port_eta):
        if pd.isna(port_eta):
            return "Not Yet Sailed / Unknown"
        if port_eta < today:
            return "Past ETA"
        if port_eta.normalize() == today:
            return "Today"
        if port_eta <= today + pd.Timedelta(days=7):
            return "Approaching (7d)"
        return "Not Yet Sailed / Unknown"

    rollup = rollup.copy()
    rollup["arrival_status"] = rollup["port_eta"].apply(_arrival_bucket)
    container_arrival = rollup.set_index("container")["arrival_status"]

    fully_complete = detail.groupby("container")["status"].apply(lambda s: set(s) == {"Complete"})
    compliance_pct = 100 * fully_complete.sum() / len(fully_complete) if len(fully_complete) else 0

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        Merges the **physical shipment world** (currently-tracked containers,
        In-Transit list) with the **invoice world** (Bills + GL), scored per
        container, per required cost category — **Ocean Freight, Customs,
        Duty, Drayage**.

        - **Complete**: a bill's invoice number is confirmed in the GL ledger
          for that category.
        - **Pending**: not confirmed yet, but the container already has an
          unresolved bill on file (a placeholder, or a real invoice just not
          posted to GL yet) — something is already in motion. **Action: chase
          the vendor for the actual bill — don't create a new placeholder.**
        - **Missing**: nothing on file at all for that category — no bill,
          no placeholder. **Action: mark a placeholder and chase the vendor
          from scratch.**

        Uses full billing history (not date-windowed) across
        **{len(tracked_containers):,} currently-tracked containers**, so a
        container shows its true current state regardless of when a bill was
        dated.
        """
    )

    # =========================================================================
    # DATA INTEGRITY CHECK — proves Missing and Pending never overlap
    # =========================================================================
    st.divider()
    st.markdown("### ✅ Data Integrity Check: Missing vs. Pending Never Overlap")
    st.caption(
        "Recomputed live from the data above every time this page loads — not a one-off "
        "claim. A container with a pending bill should never also show a Missing category."
    )

    dup_slots = detail.groupby(["container", "category"]).size()
    slots_scored_twice = int((dup_slots > 1).sum())

    containers_with_pending_bill = set(pending_bills["container"].unique())
    containers_with_missing = set(missing_summary["container"].unique())
    overlap = containers_with_pending_bill & containers_with_missing

    col1, col2 = st.columns(2)
    with col1:
        if slots_scored_twice == 0:
            st.success(f"✅ Every container × category scored exactly once ({len(detail):,} slots, 0 duplicates)")
        else:
            st.error(f"❌ {slots_scored_twice} container × category slot(s) scored more than once")
    with col2:
        if len(overlap) == 0:
            st.success(f"✅ 0 containers have both a pending bill and a Missing category")
        else:
            st.error(f"❌ {len(overlap)} container(s) have both a pending bill AND a Missing category")
            st.dataframe(
                missing_summary[missing_summary["container"].isin(overlap)],
                use_container_width=True, hide_index=True
            )

    # =========================================================================
    # HIGH PRIORITY — missing bills on containers already at/near port
    # =========================================================================
    st.divider()
    st.markdown("### 🚨 High Priority — Missing Bills, Container At/Near Port")

    missing_urgent = missing_summary.merge(
        container_arrival.rename("arrival_status"), on="container", how="left"
    )
    high_priority = missing_urgent[missing_urgent["arrival_status"].isin(["Past ETA", "Today", "Approaching (7d)"])]

    st.metric("Overall Compliance Rate", f"{compliance_pct:.1f}%", help="Containers fully Complete across all 4 categories")

    if len(high_priority) > 0:
        st.error(
            f"**{high_priority['container'].nunique()} container(s)** are already at port or "
            f"arriving within 7 days with at least one missing bill — these are urgent, not "
            f"just backlog. Everything else in Missing below can wait."
        )
        hp_display = high_priority[["container", "category", "arrival_status"]].copy()
        hp_display["category"] = hp_display["category"].map(CATEGORY_LABELS)
        hp_display = hp_display.rename(columns={"category": "Missing Category"})
        status_order = {"Past ETA": 0, "Today": 1, "Approaching (7d)": 2}
        hp_display["_sort"] = hp_display["arrival_status"].map(status_order)
        hp_display = hp_display.sort_values("_sort").drop(columns=["_sort"])
        st.dataframe(hp_display, use_container_width=True, hide_index=True)
    else:
        st.success("No missing bills on containers at/near port — nothing urgent right now.")

    # =========================================================================
    # HEADLINE: PER-CATEGORY BREAKDOWN
    # =========================================================================
    st.divider()
    st.markdown("### 📊 Per-Category Compliance")

    summary = (
        detail.groupby(["category", "status"]).size().unstack(fill_value=0)
        .reindex(columns=["Complete", "Pending", "Missing"], fill_value=0)
        .reindex(REQUIRED_CATEGORIES)
    )
    summary.index = [CATEGORY_LABELS[c] for c in summary.index]

    col1, col2 = st.columns([1, 1])
    with col1:
        st.dataframe(summary, use_container_width=True)
    with col2:
        fig = px.bar(
            summary, x=summary.index, y=["Complete", "Pending", "Missing"],
            title="Containers by Status, per Category", barmode="stack",
            color_discrete_map={"Complete": "#2ecc71", "Pending": "#f39c12", "Missing": "#e74c3c"}
        )
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # MISSING — needs a placeholder + vendor chase
    # =========================================================================
    st.divider()
    st.markdown("### 🔴 Missing — Needs a Placeholder & Vendor Chase")
    st.markdown(
        f"""
        **{len(missing_summary):,} category-slots** across
        **{missing_summary['container'].nunique():,} containers** have
        nothing on file at all. For these, mark a placeholder bill and start
        chasing the vendor — there's no bill in motion yet.
        """
    )

    if len(missing_summary) > 0:
        missing_display = missing_summary[["container", "category"]].copy()
        missing_display["category"] = missing_display["category"].map(CATEGORY_LABELS)
        missing_display["likely_supplier_contact"] = missing_display["container"].map(container_supplier)
        missing_display = missing_display.rename(columns={"category": "Missing Category"})
        missing_display = missing_display.sort_values(["container", "Missing Category"])

        cat_filter = st.multiselect(
            "Filter by category:", list(CATEGORY_LABELS.values()),
            default=list(CATEGORY_LABELS.values()), key="missing_cat_filter"
        )
        st.dataframe(
            missing_display[missing_display["Missing Category"].isin(cat_filter)],
            use_container_width=True, hide_index=True, height=400
        )
        st.caption(
            "📌 'likely_supplier_contact' is the product supplier on the shipment "
            "(not necessarily the freight vendor) — a starting-point contact when "
            "there's no bill on file yet to identify the actual freight vendor."
        )
    else:
        st.success("No missing invoices — every container has at least a bill in motion for every category.")

    # =========================================================================
    # PENDING — needs an actual bill
    # =========================================================================
    st.divider()
    st.markdown("### 🟡 Pending — Needs an Actual Bill")
    st.markdown(
        f"""
        **{len(pending_bills):,} bills** across
        **{pending_bills['container'].nunique() if len(pending_bills) > 0 else 0:,} containers**
        are already sitting on file (placeholder or real-but-unposted) — the
        team just needs to chase the vendor for the actual/confirmed invoice.
        `inferred_category` is a best guess based on that vendor's historical
        billing pattern; "Unclear" means the vendor either has no classified
        GL history or handles more than one category, so we can't confidently
        say which cost this covers.
        """
    )

    if len(pending_bills) > 0:
        pending_display = pending_bills.copy()
        pending_display["inferred_category"] = pending_display["inferred_category"].replace(CATEGORY_LABELS)
        pending_display = pending_display.sort_values(["container", "invoice_dt"])
        st.dataframe(pending_display, use_container_width=True, hide_index=True, height=400)

        unclear_count = (pending_bills["inferred_category"] == "Unclear").sum()
        if unclear_count > 0:
            st.caption(f"📌 {unclear_count} pending bill(s) have an unclear category (ambiguous or unrecognized vendor) — still real, still need chasing, just not attributable to one specific cost bucket.")
    else:
        st.success("No pending bills — nothing currently in motion.")

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔴 Download Missing List as CSV", key="missing_export_btn"):
            csv = missing_summary.to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"invoice_missing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="missing_export_download"
            )
    with col2:
        if st.button("🟡 Download Pending List as CSV", key="pending_export_btn"):
            csv = pending_bills.to_csv(index=False)
            st.download_button(
                label="Download CSV File", data=csv,
                file_name=f"invoice_pending_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv", key="pending_export_download"
            )

    st.caption(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{len(tracked_containers):,} containers | {len(missing_summary):,} missing | {len(pending_bills):,} pending bills"
    )


if __name__ == "__main__":
    st.set_page_config(page_title="Invoice Compliance", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>📋 INVOICE COMPLIANCE</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "Missing vs. Pending, per container, per category</p>",
        unsafe_allow_html=True
    )
    render_tab()
