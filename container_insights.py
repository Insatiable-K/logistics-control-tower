# -*- coding: utf-8 -*-
"""
CONTAINER INSIGHTS DASHBOARD - Merged Bills + GL, Container-Level View
Joins Bills.xls to GL (1275+1313) via invoice number, scoped to the current
operational window, and shows what each container was billed vs. what's
confirmed in the accounting ledger.
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
from utils import load_html_table, clean_bills_dataframe, clean_gl_dataframe, build_container_ledger

st.set_page_config(page_title="Container Insights", layout="wide")

st.markdown(
    "<h1 style='text-align: center; color: #2c3e50;'>📦 CONTAINER INSIGHTS</h1>"
    "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
    "What we billed vs. what's confirmed in accounting — by container</p>",
    unsafe_allow_html=True
)

# =============================================================================
# LOAD, CLEAN, MERGE
# =============================================================================
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
def load_merged(window_start, window_end):
    bills = load_bills()
    gl = load_gl()
    return build_container_ledger(bills, gl, window_start, window_end)


WINDOW_START = pd.Timestamp("2025-12-01")
WINDOW_END = pd.Timestamp.today().normalize()

merged = load_merged(WINDOW_START, WINDOW_END)
ledger = merged["container_ledger"]
matched = merged["matched_detail"]
unmatched_gl = merged["unmatched_gl"]

CATEGORY_COLS = [c for c in ["OF", "CUSTOMS", "DUTY", "DRAYAGE", "ACCESSORIAL", "Uncategorized"] if c in ledger.columns]

# =============================================================================
# SECTION 1: WHAT THIS SHOWS
# =============================================================================
st.markdown("### What This Data Represents")
st.markdown(
    f"""
    This dashboard **merges two datasets** you've already seen separately:
    - **Bills** (what vendors invoiced us, tied to a specific container)
    - **GL Accounting** (what's actually confirmed/posted in the company ledger)

    They're linked by matching invoice numbers between the two systems.

    **Time window: {WINDOW_START.strftime('%B %d, %Y')} to {WINDOW_END.strftime('%B %d, %Y')}**
    (the current operational period — older or placeholder-invoice-number
    bills are excluded from matching, since matching on values like
    "PENDING" would create false links between unrelated bills.)

    Result: **{len(ledger):,} containers** with recent activity, tracked
    from bill to ledger confirmation.
    """
)

# =============================================================================
# SECTION 2: OVERVIEW KPIS
# =============================================================================
st.divider()
st.markdown("### 💰 Overview")

total_billed = ledger["total_billed"].sum()
total_confirmed = ledger["total_gl_confirmed"].sum()
overall_coverage = 100 * total_confirmed / total_billed if total_billed > 0 else 0

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Containers in Window", f"{len(ledger):,}")
with col2:
    st.metric("Total Billed", f"${total_billed:,.0f}")
with col3:
    st.metric("Confirmed in Ledger", f"${total_confirmed:,.0f}")
with col4:
    st.metric("Overall Coverage", f"{overall_coverage:.0f}%")

st.markdown(
    """
    💡 **What is "coverage"?** It's how much of what we billed has actually
    shown up in the accounting ledger yet. Low coverage on a recently-billed
    container isn't necessarily a problem — it just means the accounting
    team hasn't posted it yet. Very low coverage on an *older* bill in this
    window is worth a follow-up.
    """
)

# =============================================================================
# SECTION 3: CONTAINER-LEVEL TABLE
# =============================================================================
st.divider()
st.markdown("### 📋 Container-Level Detail")
st.markdown("Every container active in this window — sortable by any column.")

display_cols = ["container", "total_billed", "total_gl_confirmed", "gl_coverage_pct",
                 "bill_count", "gl_matched_count", "awaiting_gl_count", "vendors"] + CATEGORY_COLS
display_df = ledger[display_cols].copy().sort_values("total_billed", ascending=False)
display_df["gl_coverage_pct"] = display_df["gl_coverage_pct"].round(0)

st.dataframe(display_df, use_container_width=True, hide_index=True, height=400)

# =============================================================================
# SECTION 4: COST BREAKDOWN BY CATEGORY
# =============================================================================
st.divider()
st.markdown("### 💸 Cost Breakdown by Category (All Containers)")

category_totals = ledger[CATEGORY_COLS].sum().sort_values(ascending=False)
category_totals = category_totals[category_totals > 0]

col1, col2 = st.columns([1, 1])
with col1:
    fig = px.pie(values=category_totals.values, names=category_totals.index,
                 title="Where confirmed freight cost goes")
    st.plotly_chart(fig, use_container_width=True)
with col2:
    st.markdown("**Breakdown:**")
    for cat, amount in category_totals.items():
        pct = 100 * amount / category_totals.sum()
        st.write(f"- **{cat}**: ${amount:,.0f} ({pct:.0f}%)")

# =============================================================================
# SECTION 5: TOP CONTAINERS
# =============================================================================
st.divider()
st.markdown("### 🔝 Top Containers")

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Highest Cost Containers**")
    top_cost = ledger.nlargest(10, "total_billed")[["container", "total_billed", "gl_coverage_pct"]]
    fig = px.bar(top_cost, x="total_billed", y="container", orientation="h",
                 title="Top 10 by Total Billed $")
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.markdown("**Lowest Ledger Coverage (Recently Billed)**")
    st.caption("These have real bills but little/no confirmation in accounting yet — worth a follow-up.")
    low_coverage = ledger[ledger["bill_count"] > 0].nsmallest(10, "gl_coverage_pct")[
        ["container", "total_billed", "gl_coverage_pct", "bill_count"]
    ]
    st.dataframe(low_coverage, use_container_width=True, hide_index=True)

# =============================================================================
# SECTION 6: VENDOR VIEW
# =============================================================================
st.divider()
st.markdown("### 🏢 Vendors Driving Container Cost")

vendor_expanded = ledger.assign(vendor=ledger["vendors"].str.split(" | ", regex=False)).explode("vendor")
vendor_expanded = vendor_expanded[vendor_expanded["vendor"].notna() & (vendor_expanded["vendor"] != "")]
vendor_cost = vendor_expanded.groupby("vendor")["total_billed"].sum().nlargest(15)

fig = px.bar(x=vendor_cost.values, y=vendor_cost.index, orientation="h",
             title="Top 15 Vendors by Container-Level Billed Amount")
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "📌 Note: a container can have multiple vendors (ocean freight, customs, drayage, etc.), "
    "so this counts each vendor's contribution separately per container."
)

# =============================================================================
# SECTION 7: 1275 TRACEABILITY
# =============================================================================
st.divider()
st.markdown("### 🏷️ Capitalized Freight Traceability (GL 1275)")

notes_containers = ledger[ledger["notes"] != ""]

st.markdown(
    f"""
    **{len(notes_containers)} containers** in this window have costs posted to
    **GL 1275 (Capitalized Inventory Freight)** — the account for freight
    costs large/significant enough to be capitalized onto the balance sheet
    as part of inventory value, rather than expensed immediately (like GL
    1313, Prepaid Container Freight).

    Because the accounting ledger itself has no container field, each 1275
    match below carries an explicit **note** linking it back to the
    physical shipment (container + SIPL) — so finance can trace a
    capitalized dollar amount back to what it actually paid for.
    """
)

if len(notes_containers) > 0:
    st.dataframe(
        notes_containers[["container", "total_billed", "total_gl_confirmed", "notes"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No GL 1275 matches found in this window.")

# =============================================================================
# SECTION 8: UNMATCHED GL ACTIVITY
# =============================================================================
st.divider()
st.markdown("### ⚠️ Ledger Activity Not Yet Tied to a Container")

unmatched_amount = unmatched_gl["net_amount"].sum()
st.markdown(
    f"""
    **{len(unmatched_gl):,} ledger entries** (${unmatched_amount:,.0f}) in this
    window have a real invoice number but **don't match any bill** in this
    same window.

    💡 This is expected and not necessarily an error — it usually means:
    - The bill was entered outside this date window (earlier or later)
    - It's a direct accounting entry (journal, adjustment) with no matching bill record
    - The container it relates to isn't part of this window's active shipments

    We report this honestly rather than force-fitting it into the container
    view above.

    ℹ️ **A negative dollar figure here doesn't mean a refund landed in our
    bank account** — GL amounts are "debit minus credit." A negative total
    means credit-side entries (reversals, corrections, reclassifications)
    outweigh debit-side charges in that group, not that we received cash back.
    """
)

col1, col2 = st.columns(2)
with col1:
    unmatched_by_account = unmatched_gl.groupby("account")["net_amount"].agg(["sum", "count"])
    st.markdown("**By Account:**")
    for account in unmatched_by_account.index:
        amt, cnt = unmatched_by_account.loc[account]
        st.write(f"- {account}: {cnt:,} entries, ${amt:,.0f}")

with col2:
    unmatched_by_category = unmatched_gl.groupby("category")["net_amount"].sum().sort_values(ascending=False)
    st.markdown("**By Category:**")
    for cat, amt in unmatched_by_category.items():
        st.write(f"- {cat}: ${amt:,.0f}")

# =============================================================================
# SECTION 9: DATA QUALITY CARRYOVER
# =============================================================================
st.divider()
st.markdown("### 🔍 Follow-Up Items")

zero_coverage = ledger[(ledger["total_billed"] > 0) & (ledger["gl_coverage_pct"] == 0)]
if len(zero_coverage) > 0:
    st.warning(
        f"**{len(zero_coverage)} containers** have bills in this window but **zero** "
        f"confirmed ledger amount (${zero_coverage['total_billed'].sum():,.0f} total). "
        "These may just be too recent to be posted yet, or worth checking."
    )
    with st.expander("View containers with zero GL coverage"):
        st.dataframe(
            zero_coverage[["container", "total_billed", "bill_count", "vendors"]],
            use_container_width=True, hide_index=True
        )

awaiting_total = ledger["awaiting_gl_count"].sum()
if awaiting_total > 0:
    st.info(
        f"**{int(awaiting_total)} bills** in this window still have a placeholder/pending "
        "invoice number (PENDING, XENDING, etc.) and were excluded from matching — "
        "these need a real invoice number before they can be confirmed against the ledger."
    )

# =============================================================================
# SECTION 10: EXPORT
# =============================================================================
st.divider()
st.markdown("### 📥 Download This Data")

if st.button("📊 Download Container Ledger as CSV"):
    csv = ledger.to_csv(index=False)
    st.download_button(
        label="Download CSV File",
        data=csv,
        file_name=f"container_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

st.caption(
    f"Window: {WINDOW_START.strftime('%Y-%m-%d')} to {WINDOW_END.strftime('%Y-%m-%d')} | "
    f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
