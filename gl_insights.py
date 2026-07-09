# -*- coding: utf-8 -*-
"""
GL INSIGHTS DASHBOARD
Standalone Streamlit exploratory dashboard for GL accounts (1275 + 1313).
Senior logistics management view — understand freight cost accounting
independent of container matching (GL has no container field by design).
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, standardize_columns, clean_currency, clean_date,
    clean_text, normalize_category, REQUIRED_CATEGORIES, ACCESSORIAL_LABEL
)

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="GL Insights",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(
    "<h1 style='text-align: center;'>GL INSIGHTS DASHBOARD</h1>"
    "<p style='text-align: center; color: #666;'>"
    "Senior Logistics Management View — Freight cost accounting (GL 1275 & 1313)</p>",
    unsafe_allow_html=True
)

# =============================================================================
# LOAD & CLEAN DATA
# =============================================================================
@st.cache_data
def load_and_clean_gl():
    """Load GL 1275 & 1313 and perform cleaning."""

    # Load both GL accounts
    gl_1275 = load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls"))
    gl_1313 = load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls"))

    # Standardize columns
    gl_1275 = standardize_columns(gl_1275)
    gl_1313 = standardize_columns(gl_1313)

    # Add account tag
    gl_1275["account"] = "1275"
    gl_1313["account"] = "1313"

    # Combine
    gl = pd.concat([gl_1275, gl_1313], ignore_index=True)
    original_count = len(gl)

    # Clean currency columns (debit/credit separate, then net amount)
    if "debit" in gl.columns:
        gl["debit"] = clean_currency(gl["debit"])
    if "credit" in gl.columns:
        gl["credit"] = clean_currency(gl["credit"])

    gl["net_amount"] = gl["debit"].fillna(0) - gl["credit"].fillna(0)

    # Clean date column
    if "date" in gl.columns:
        gl["date"] = clean_date(gl["date"])

    # Clean text fields
    for col in ["party", "description", "location", "division"]:
        if col in gl.columns:
            gl[col] = clean_text(gl[col])

    # Classify descriptions
    gl["category"] = gl["description"].apply(normalize_category)

    # Identify adjustments/reversals
    gl["is_adjustment"] = gl["description"].astype(str).str.contains(
        "ADJUSTMENT|CREDIT MEMO|REVERSAL|VOID",
        case=False,
        na=False
    ) | (gl["type"].isin(["Credit Memo", "Supplier Credit Memo"]))

    # Identify junk rows (zero debit AND zero credit)
    gl["is_junk"] = (gl["debit"] == 0) & (gl["credit"] == 0)

    return gl, original_count


# Load data
gl_combined, original_gl_count = load_and_clean_gl()

# Split by account for display
gl_1275 = gl_combined[gl_combined["account"] == "1275"]
gl_1313 = gl_combined[gl_combined["account"] == "1313"]

with st.sidebar:
    st.markdown("### Data Overview")
    st.write(f"**GL 1275 rows:** {len(gl_1275):,}")
    st.write(f"**GL 1313 rows:** {len(gl_1313):,}")
    st.write(f"**Combined:** {len(gl_combined):,}")

# =============================================================================
# SECTION 1: ACCOUNT COMPARISON OVERVIEW
# =============================================================================
st.header("1. Account Comparison Overview")

col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("GL 1275 (Capitalized Inventory Freight)")
    st.write(f"**Rows:** {len(gl_1275):,}")
    total_1275 = gl_1275["net_amount"].sum()
    st.write(f"**Net Amount:** ${total_1275:,.0f}")

with col2:
    st.subheader("GL 1313 (Prepaid Container Freight)")
    st.write(f"**Rows:** {len(gl_1313):,}")
    total_1313 = gl_1313["net_amount"].sum()
    st.write(f"**Net Amount:** ${total_1313:,.0f}")

with col3:
    st.subheader("Combined")
    st.write(f"**Rows:** {len(gl_combined):,}")
    total_combined = gl_combined["net_amount"].sum()
    st.write(f"**Net Amount:** ${total_combined:,.0f}")

# Type distribution
st.subheader("Transaction Type Distribution")
col1, col2, col3 = st.columns(3)

with col1:
    st.write("**GL 1275:**")
    type_1275 = gl_1275["type"].value_counts()
    for ttype, count in type_1275.items():
        st.write(f"- {ttype}: {count:,}")

with col2:
    st.write("**GL 1313:**")
    type_1313 = gl_1313["type"].value_counts()
    for ttype, count in type_1313.items():
        st.write(f"- {ttype}: {count:,}")

with col3:
    st.write("**Reconciled (1313 only):**")
    recon = gl_1313["reconciled"].value_counts() if "reconciled" in gl_1313.columns else None
    if recon is not None:
        for status, count in recon.items():
            st.write(f"- {status}: {count:,} (100%)" if count == len(gl_1313) else f"- {status}: {count:,}")

# =============================================================================
# SECTION 2: CATEGORY BREAKDOWN
# =============================================================================
st.header("2. Freight Cost Category Breakdown")

# Classification by account
col1, col2 = st.columns(2)

with col1:
    st.subheader("GL 1275 by Category")
    cat_1275 = gl_1275.groupby("category")["net_amount"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    for cat in cat_1275.index:
        amount, count = cat_1275.loc[cat]
        st.write(f"**{cat or 'Uncategorized'}:** ${amount:,.0f} ({count:,} entries)")

with col2:
    st.subheader("GL 1313 by Category")
    cat_1313 = gl_1313.groupby("category")["net_amount"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    for cat in cat_1313.index:
        amount, count = cat_1313.loc[cat]
        st.write(f"**{cat or 'Uncategorized'}:** ${amount:,.0f} ({count:,} entries)")

# Visualization
col1, col2 = st.columns(2)

with col1:
    cat_totals_1275 = gl_1275.groupby("category")["net_amount"].sum().sort_values(ascending=False)
    fig = px.pie(
        values=cat_totals_1275.values,
        names=cat_totals_1275.index,
        title="GL 1275 Category Distribution"
    )
    st.plotly_chart(fig, use_container_width=True)

with col2:
    cat_totals_1313 = gl_1313.groupby("category")["net_amount"].sum().sort_values(ascending=False)
    fig = px.pie(
        values=cat_totals_1313.values,
        names=cat_totals_1313.index,
        title="GL 1313 Category Distribution"
    )
    st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# SECTION 3: VENDOR (PARTY) ANALYSIS
# =============================================================================
st.header("3. Vendor (Party) Analysis")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Top 15 Vendors by Amount (GL 1275)")
    vendor_1275 = gl_1275.groupby("party")["net_amount"].sum().nlargest(15)
    fig = px.bar(x=vendor_1275.values, y=vendor_1275.index, orientation="h")
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Top 15 Vendors by Amount (GL 1313)")
    vendor_1313 = gl_1313.groupby("party")["net_amount"].sum().nlargest(15)
    fig = px.bar(x=vendor_1313.values, y=vendor_1313.index, orientation="h")
    st.plotly_chart(fig, use_container_width=True)

# Detailed vendor stats
with st.expander("Detailed Vendor Statistics (All Accounts Combined)"):
    vendor_stats = gl_combined.groupby("party").agg({
        "invoice": "count",
        "debit": "sum",
        "credit": "sum",
        "net_amount": "sum"
    }).round(2)
    vendor_stats.columns = ["Entries", "Total Debit", "Total Credit", "Net Amount"]
    vendor_stats = vendor_stats.sort_values("Net Amount", ascending=False)
    st.dataframe(vendor_stats, use_container_width=True)

# =============================================================================
# SECTION 4: RUNNING BALANCE TREND
# =============================================================================
st.header("4. Running Balance Trend (Freight Asset View)")

if "balance" in gl_combined.columns:
    col1, col2 = st.columns(2)

    with col1:
        # GL 1275 balance over time
        balance_1275 = gl_1275[gl_1275["balance"].notna()].sort_values("date")
        if len(balance_1275) > 0:
            fig = go.Figure(data=[
                go.Scatter(x=balance_1275["date"], y=balance_1275["balance"], mode="lines", name="Balance")
            ])
            fig.update_layout(
                title="GL 1275 Cumulative Balance Over Time",
                xaxis_title="Date",
                yaxis_title="Balance ($)"
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        # GL 1313 balance over time
        balance_1313 = gl_1313[gl_1313["balance"].notna()].sort_values("date")
        if len(balance_1313) > 0:
            fig = go.Figure(data=[
                go.Scatter(x=balance_1313["date"], y=balance_1313["balance"], mode="lines", name="Balance", line=dict(color="orange"))
            ])
            fig.update_layout(
                title="GL 1313 Cumulative Balance Over Time",
                xaxis_title="Date",
                yaxis_title="Balance ($)"
            )
            st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# SECTION 5: ADJUSTMENTS & REVERSALS
# =============================================================================
st.header("5. Adjustments & Reversals (Dispute/Rework Signal)")

adj_1275 = gl_1275[gl_1275["is_adjustment"]]
adj_1313 = gl_1313[gl_1313["is_adjustment"]]

col1, col2 = st.columns(2)

with col1:
    adj_count_1275 = len(adj_1275)
    adj_amount_1275 = adj_1275["net_amount"].sum()
    st.metric("GL 1275 Adjustments", f"{adj_count_1275} entries, ${adj_amount_1275:,.0f}")
    if adj_count_1275 > 0:
        adj_vendor_1275 = adj_1275.groupby("party")["net_amount"].sum().nlargest(5)
        st.write("Top vendors with adjustments:")
        for vendor, amount in adj_vendor_1275.items():
            st.write(f"- {vendor}: ${amount:,.0f}")

with col2:
    adj_count_1313 = len(adj_1313)
    adj_amount_1313 = adj_1313["net_amount"].sum()
    st.metric("GL 1313 Adjustments", f"{adj_count_1313} entries, ${adj_amount_1313:,.0f}")
    if adj_count_1313 > 0:
        adj_vendor_1313 = adj_1313.groupby("party")["net_amount"].sum().nlargest(5)
        st.write("Top vendors with adjustments:")
        for vendor, amount in adj_vendor_1313.items():
            st.write(f"- {vendor}: ${amount:,.0f}")

# =============================================================================
# SECTION 6: LOCATION / DIVISION BREAKDOWN
# =============================================================================
st.header("6. Freight Cost by Location & Division")

if "location" in gl_combined.columns:
    col1, col2 = st.columns(2)

    with col1:
        location_amount = gl_combined.groupby("location")["net_amount"].sum().sort_values(ascending=False).head(15)
        fig = px.bar(
            x=location_amount.values,
            y=location_amount.index,
            orientation="h",
            title="Top 15 Locations by Freight Cost"
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        if "division" in gl_combined.columns:
            division_amount = gl_combined.groupby("division")["net_amount"].sum().sort_values(ascending=False)
            fig = px.bar(
                x=division_amount.values,
                y=division_amount.index,
                orientation="h",
                title="Divisions by Freight Cost"
            )
            st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# SECTION 7: TIME SERIES (POSTINGS & $)
# =============================================================================
st.header("7. Time Series Analysis")

if "date" in gl_combined.columns:
    gl_combined["week"] = gl_combined["date"].dt.to_period("W")

    col1, col2 = st.columns(2)

    with col1:
        # Postings per week
        postings_by_week = gl_combined.groupby("week").size()
        fig = go.Figure(data=[
            go.Scatter(x=postings_by_week.index.astype(str), y=postings_by_week.values, mode="lines+markers")
        ])
        fig.update_layout(title="GL Postings per Week", xaxis_title="Week", yaxis_title="Count")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Amount per week (by account)
        amount_by_week_1275 = gl_1275.groupby("week")["net_amount"].sum()
        amount_by_week_1313 = gl_1313.groupby("week")["net_amount"].sum()

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=amount_by_week_1275.index.astype(str), y=amount_by_week_1275.values, name="GL 1275"))
        fig.add_trace(go.Scatter(x=amount_by_week_1313.index.astype(str), y=amount_by_week_1313.values, name="GL 1313"))
        fig.update_layout(title="Net Amount per Week by Account", xaxis_title="Week", yaxis_title="$ Amount")
        st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# SECTION 8: DATA QUALITY & ANOMALIES
# =============================================================================
st.header("8. Data Quality & Anomalies")

anomalies_found = False

# Junk rows (zero debit and zero credit)
junk_count = gl_combined["is_junk"].sum()
if junk_count > 0:
    anomalies_found = True
    st.warning(f"**{junk_count:,} rows with zero debit AND zero credit (dead rows)**")

# Uncategorized descriptions
uncategorized = gl_combined[gl_combined["category"].isna()]
if len(uncategorized) > 0:
    anomalies_found = True
    st.info(f"**{len(uncategorized):,} rows with uncategorized description ({100*len(uncategorized)/len(gl_combined):.1f}%)**")
    with st.expander("View sample uncategorized entries"):
        st.dataframe(
            uncategorized[["date", "type", "description", "party"]].drop_duplicates("description").head(20),
            use_container_width=True,
            hide_index=True
        )

# Duplicate transaction IDs
if "transaction" in gl_combined.columns:
    dup_trans = gl_combined["transaction"].value_counts()
    dup_count = (dup_trans > 1).sum()
    if dup_count > 0:
        anomalies_found = True
        st.info(f"**{dup_count:,} unique transaction IDs appearing multiple times** (expected — multi-line entries)")

# Negative balances (if any)
if "balance" in gl_combined.columns:
    neg_balance = (gl_combined["balance"] < 0).sum()
    if neg_balance > 0:
        anomalies_found = True
        st.warning(f"**{neg_balance:,} rows with negative balance**")

# Very old entries
if "date" in gl_combined.columns:
    very_old = (gl_combined["date"] < pd.Timestamp("2024-01-01")).sum()
    if very_old > 0:
        st.info(f"**{very_old:,} entries from before 2024** (included in analysis)")

if not anomalies_found and junk_count == 0:
    st.success("No major data quality issues detected")

# =============================================================================
# SECTION 9: CONTAINER LINKAGE NOTE
# =============================================================================
st.header("9. Container Linkage Note")

st.info(
    "**GL rows have NO direct container field.** Container linkage occurs only "
    "transitively via GL `invoice` number → Bills.xls `bill_inv` → Bills.xls `container`. "
    "This join is a deliberately separate next step, not part of this dashboard. "
    "Both datasets (Bills and GL) are analyzed independently here to first understand "
    "their structure and quality before any cross-referencing."
)

# =============================================================================
# FOOTER
# =============================================================================
st.divider()
st.caption(
    "GL Insights Dashboard | "
    f"GL 1275: {len(gl_1275):,} rows | "
    f"GL 1313: {len(gl_1313):,} rows | "
    f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
