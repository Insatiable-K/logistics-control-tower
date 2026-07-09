# -*- coding: utf-8 -*-
"""
BILLS INSIGHTS DASHBOARD
Standalone Streamlit exploratory dashboard for Bills.xls dataset.
Senior logistics management view — understand invoice/bill patterns
independent of container matching.
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
    load_html_table, standardize_columns, clean_container, clean_currency,
    clean_date, clean_text, is_pending
)

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Bills Insights",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(
    "<h1 style='text-align: center;'>BILLS INSIGHTS DASHBOARD</h1>"
    "<p style='text-align: center; color: #666;'>"
    "Senior Logistics Management View — Comprehensive analysis of invoice/bill dataset</p>",
    unsafe_allow_html=True
)

# =============================================================================
# LOAD & CLEAN DATA
# =============================================================================
@st.cache_data
def load_and_clean_bills():
    """Load Bills.xls and perform thorough cleaning per logistics requirements."""

    # Load HTML table
    bills_raw = load_html_table(Path("Bills.xls"))
    original_count = len(bills_raw)

    # Standardize column names
    bills = standardize_columns(bills_raw)

    # Track cleaning steps
    steps = {
        "Original": original_count,
        "After standardization": len(bills)
    }

    # Step 1: Remove rows with no container marker (but separate air freight)
    before = len(bills)
    bills[bills["container"] != bills["container"]] = bills["container"].isna()
    bills = bills[bills["container"].notna() & (bills["container"].astype(str).str.strip() != "")]
    after = len(bills)
    steps["No container field"] = before - after

    # Step 2: Remove rows with no invoice number
    before = len(bills)
    bills = bills[bills["bill_inv"].notna() & (bills["bill_inv"].astype(str).str.strip() != "")]
    after = len(bills)
    steps["No invoice number"] = before - after

    # Step 3: Remove duplicate bills (by container+invoice)
    before = len(bills)
    bills = bills.drop_duplicates(subset=["container", "bill_inv"], keep="first")
    after = len(bills)
    steps["Exact duplicates"] = before - after

    # Step 4: Clean container — use regex to extract ISO 6346 codes
    # Keep air freight as a separate category
    before = len(bills)
    bills["container_clean"] = bills["container"].apply(lambda x: clean_container(x, keep_air_freight_marker=True))
    bills = bills[bills["container_clean"].notna()]
    after = len(bills)
    steps["Invalid ISO 6346"] = before - after

    # Step 5: Clean amount and other currency fields
    if "amount" in bills.columns:
        bills["amount"] = clean_currency(bills["amount"])
    if "balance_due" in bills.columns:
        bills["balance_due"] = clean_currency(bills["balance_due"])
    if "sipl_amount" in bills.columns:
        bills["sipl_amount"] = clean_currency(bills["sipl_amount"])

    # Step 6: Clean date fields
    for col in bills.columns:
        if "date" in col.lower() or "dt" in col.lower():
            if bills[col].dtype == "object":
                bills[col] = clean_date(bills[col])

    # Step 7: Remove zero/null amount rows BUT KEEP THEM FLAGGED
    # (don't remove, we need to report them as anomalies)
    bills["has_zero_amount"] = (bills["amount"] == 0) | bills["amount"].isna()

    # Step 8: Identify pending bills and placeholder invoice patterns
    bills["is_pending_literal"] = is_pending(bills["bill_inv"])
    bills["is_placeholder_junk"] = bills["bill_inv"].astype(str).apply(
        lambda x: str(x).strip().upper() in ["XENDING", "1", "2", "12", "POSTED", "DUPLICATE"]
    )
    bills["is_air_freight"] = bills["container_clean"] == "AIR FREIGHT"

    # Rename for consistency
    bills = bills.rename(columns={"container_clean": "container"})

    # Final summary
    final_count = len(bills)
    retention = 100 * final_count / original_count

    return bills, original_count, final_count, retention, steps


# Load data
bills, original_rows, cleaned_rows, retention_pct, cleaning_steps = load_and_clean_bills()

# Display cleaning waterfall in sidebar
with st.sidebar:
    st.markdown("### Cleaning Waterfall")
    st.write(f"**Original rows:** {original_rows:,}")
    for step_name, rows_removed in cleaning_steps.items():
        if step_name != "Original" and step_name != "After standardization":
            if rows_removed > 0:
                st.write(f"- {step_name}: -{rows_removed:,}")
    st.write(f"\n**Final cleaned rows:** {cleaned_rows:,}")
    st.write(f"**Retention:** {retention_pct:.1f}%")

# =============================================================================
# SECTION 1: OVERVIEW KPIS
# =============================================================================
st.header("1. Overview KPIs")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Bills", f"{len(bills):,}")
with col2:
    st.metric("Unique Containers", bills["container"].nunique())
with col3:
    st.metric("Unique Invoices", bills["bill_inv"].nunique())
with col4:
    st.metric("Unique Vendors", bills["non_inventory_vendor"].nunique() if "non_inventory_vendor" in bills.columns else "N/A")

col1, col2, col3, col4 = st.columns(4)
with col1:
    total_amount = bills["amount"].sum()
    st.metric("Total Amount", f"${total_amount:,.0f}")
with col2:
    avg_amount = bills["amount"].mean()
    st.metric("Avg Bill", f"${avg_amount:,.0f}")
with col3:
    min_amount = bills["amount"].min()
    st.metric("Min Bill", f"${min_amount:,.0f}")
with col4:
    max_amount = bills["amount"].max()
    st.metric("Max Bill", f"${max_amount:,.0f}")

# =============================================================================
# SECTION 2: AP FINANCIAL VIEW
# =============================================================================
st.header("2. AP Financial View")

col1, col2 = st.columns(2)

with col1:
    # Amount vs Balance Due
    total_invoiced = bills["amount"].sum()
    total_due = bills["balance_due"].sum() if "balance_due" in bills.columns else 0
    total_paid = total_invoiced - total_due

    fig = go.Figure(data=[
        go.Bar(x=["Invoiced", "Outstanding", "Paid"], y=[total_invoiced, total_due, total_paid],
               marker_color=["#1f77b4", "#ff7f0e", "#2ca02c"])
    ])
    fig.update_layout(title="Invoice Status", yaxis_title="Amount ($)", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    # Freight cost as % of shipment value (where available)
    bills_with_sipl = bills[bills["sipl_amount"].notna() & (bills["sipl_amount"] > 0)].copy()
    if len(bills_with_sipl) > 0:
        bills_with_sipl["freight_pct"] = (bills_with_sipl["amount"] / bills_with_sipl["sipl_amount"] * 100).clip(0, 100)
        avg_pct = bills_with_sipl["freight_pct"].mean()
        fig = go.Figure(data=[go.Histogram(x=bills_with_sipl["freight_pct"], nbins=50)])
        fig.update_layout(
            title=f"Freight Cost as % of Shipment Value (Avg: {avg_pct:.1f}%)",
            xaxis_title="Freight %",
            yaxis_title="Count"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("SIPL amount data not available for this analysis")

# =============================================================================
# SECTION 3: VENDOR CONCENTRATION
# =============================================================================
st.header("3. Vendor Concentration (Pareto Analysis)")

if "non_inventory_vendor" in bills.columns:
    col1, col2 = st.columns(2)

    with col1:
        # Top vendors by count
        vendor_counts = bills["non_inventory_vendor"].value_counts().head(15)
        fig = px.bar(
            x=vendor_counts.values,
            y=vendor_counts.index,
            orientation="h",
            title="Top 15 Vendors by Bill Count",
            labels={"x": "# Bills", "y": "Vendor"}
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Top vendors by amount
        vendor_amount = bills.groupby("non_inventory_vendor")["amount"].sum().nlargest(15)
        fig = px.bar(
            x=vendor_amount.values,
            y=vendor_amount.index,
            orientation="h",
            title="Top 15 Vendors by Total Amount",
            labels={"x": "Total $", "y": "Vendor"}
        )
        st.plotly_chart(fig, use_container_width=True)

    # Detailed vendor stats table
    with st.expander("Detailed Vendor Statistics"):
        vendor_stats = bills.groupby("non_inventory_vendor").agg({
            "bill_inv": "count",
            "amount": ["sum", "mean", "min", "max"],
            "container": "nunique"
        }).round(2)
        vendor_stats.columns = ["Bill Count", "Total $", "Avg $", "Min $", "Max $", "Unique Containers"]
        vendor_stats = vendor_stats.sort_values("Total $", ascending=False)
        st.dataframe(vendor_stats, use_container_width=True)

# =============================================================================
# SECTION 4: PENDING & PLACEHOLDER INVOICE TRACKING
# =============================================================================
st.header("4. Pending & Placeholder Invoice Tracking")

col1, col2 = st.columns(2)

with col1:
    # Literal PENDING
    pending_count = bills["is_pending_literal"].sum()
    pending_amount = bills[bills["is_pending_literal"]]["amount"].sum()
    st.metric("Literal PENDING Bills", f"{pending_count} (${pending_amount:,.0f})")

with col2:
    # Junk placeholder invoices
    junk_count = bills["is_placeholder_junk"].sum()
    junk_amount = bills[bills["is_placeholder_junk"]]["amount"].sum()
    st.metric("Junk Placeholders (XENDING, 1, 2, etc.)", f"{junk_count} (${junk_amount:,.0f})")

# Detail tables
col1, col2 = st.columns(2)

with col1:
    if pending_count > 0:
        st.subheader("Literal PENDING Bills")
        pending_df = bills[bills["is_pending_literal"]][
            ["container", "bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]
        ].head(20)
        st.dataframe(pending_df, use_container_width=True, hide_index=True)

with col2:
    if junk_count > 0:
        st.subheader("Junk Placeholder Invoices")
        junk_df = bills[bills["is_placeholder_junk"]][
            ["container", "bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]
        ].head(20)
        st.dataframe(junk_df, use_container_width=True, hide_index=True)

# =============================================================================
# SECTION 5: AIR FREIGHT (NON-CONTAINER)
# =============================================================================
st.header("5. Air Freight Shipments (Non-Container)")

air_count = bills["is_air_freight"].sum()
air_amount = bills[bills["is_air_freight"]]["amount"].sum()

col1, col2 = st.columns(2)
with col1:
    st.metric("Air Freight Bills", f"{air_count} (${air_amount:,.0f})")
with col2:
    if air_count > 0:
        air_pct = 100 * air_count / len(bills)
        st.metric("% of Total Bills", f"{air_pct:.1f}%")

if air_count > 0:
    st.subheader("Air Freight Detail")
    air_df = bills[bills["is_air_freight"]][
        ["bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier", "invoice_dt"]
    ].sort_values("amount", ascending=False)
    st.dataframe(air_df, use_container_width=True, hide_index=True)

    if "non_inventory_vendor" in bills.columns:
        air_vendor = bills[bills["is_air_freight"]]["non_inventory_vendor"].value_counts().head(10)
        fig = px.bar(x=air_vendor.values, y=air_vendor.index, orientation="h", title="Top Air Freight Vendors")
        st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# SECTION 6: CONTAINER PATTERN ANALYSIS
# =============================================================================
st.header("6. Container Pattern Analysis (ISO 6346)")

containerized = bills[~bills["is_air_freight"]]

col1, col2 = st.columns(2)

with col1:
    # Container prefix distribution (top 20)
    containerized["prefix"] = containerized["container"].str[:4]
    prefix_dist = containerized["prefix"].value_counts().head(20)

    fig = px.bar(
        x=prefix_dist.values,
        y=prefix_dist.index,
        orientation="h",
        title="Top 20 Container Owner Codes (Prefixes)",
        labels={"x": "Count", "y": "Prefix"}
    )
    st.plotly_chart(fig, use_container_width=True)

with col2:
    # Bills per container distribution
    bills_per_container = containerized["container"].value_counts()
    fig = px.histogram(
        bills_per_container.values,
        nbins=40,
        title="Distribution of Bills per Container",
        labels={"value": "Bills per Container", "count": "Containers"}
    )
    st.plotly_chart(fig, use_container_width=True)

# Top containers by bill count and amount
col1, col2 = st.columns(2)

with col1:
    st.subheader("Top 15 Containers by Bill Count")
    top_count = bills_per_container.nlargest(15)
    top_count_df = pd.DataFrame({
        "Container": top_count.index,
        "Bills": top_count.values
    })
    st.dataframe(top_count_df, use_container_width=True, hide_index=True)

with col2:
    st.subheader("Top 15 Containers by Total Amount")
    container_amount = containerized.groupby("container")["amount"].sum().nlargest(15)
    container_amount_df = pd.DataFrame({
        "Container": container_amount.index,
        "Total Amount": [f"${x:,.0f}" for x in container_amount.values]
    })
    st.dataframe(container_amount_df, use_container_width=True, hide_index=True)

# =============================================================================
# SECTION 7: TIME SERIES ANALYSIS
# =============================================================================
st.header("7. Time Series Analysis")

# Find date column
date_col = None
for col in ["invoice_dt", "created_date", "date"]:
    if col in bills.columns and bills[col].dtype == "datetime64[ns]":
        date_col = col
        break

if date_col:
    bills["week"] = bills[date_col].dt.to_period("W")

    col1, col2 = st.columns(2)

    with col1:
        # Bills per week
        bills_by_week = bills.groupby("week").size()
        fig = go.Figure(data=[
            go.Scatter(x=bills_by_week.index.astype(str), y=bills_by_week.values, mode="lines+markers")
        ])
        fig.update_layout(title="Bills per Week", xaxis_title="Week", yaxis_title="Count")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Amount per week
        amount_by_week = bills.groupby("week")["amount"].sum()
        fig = go.Figure(data=[
            go.Scatter(x=amount_by_week.index.astype(str), y=amount_by_week.values, mode="lines+markers", fill="tozeroy")
        ])
        fig.update_layout(title="Amount per Week", xaxis_title="Week", yaxis_title="$ Amount")
        st.plotly_chart(fig, use_container_width=True)

    # Payment terms (invoice date to due date)
    if "due_date" in bills.columns:
        bills["days_to_due"] = (bills["due_date"] - bills[date_col]).dt.days
        bills["days_to_due"] = bills["days_to_due"].clip(-30, 120)  # Reasonable range

        fig = px.histogram(
            bills,
            x="days_to_due",
            nbins=30,
            title="Payment Terms Distribution (Invoice Date to Due Date)",
            labels={"days_to_due": "Days to Payment Due", "count": "Bills"}
        )
        st.plotly_chart(fig, use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Avg Days to Due", f"{bills['days_to_due'].mean():.0f}")
        with col2:
            st.metric("Median Days to Due", f"{bills['days_to_due'].median():.0f}")

else:
    st.info("Date columns not properly formatted for time series analysis")

# =============================================================================
# SECTION 8: DATA QUALITY & ANOMALIES
# =============================================================================
st.header("8. Data Quality & Anomalies")

anomalies_found = False

# Zero amounts
zero_count = bills["has_zero_amount"].sum()
if zero_count > 0:
    anomalies_found = True
    st.warning(f"**{zero_count:,} bills with zero or null amount**")
    with st.expander("View zero-amount bills"):
        zero_df = bills[bills["has_zero_amount"]][
            ["container", "bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]
        ].head(50)
        st.dataframe(zero_df, use_container_width=True, hide_index=True)

# Duplicate invoice numbers
dup_inv = bills["bill_inv"].value_counts()
dup_count = (dup_inv > 1).sum()
if dup_count > 0:
    anomalies_found = True
    st.warning(f"**{dup_count:,} unique invoice numbers appearing multiple times**")
    with st.expander("View duplicate invoice numbers"):
        dup_invoices = dup_inv[dup_inv > 1].head(20)
        for inv, count in dup_invoices.items():
            dup_rows = bills[bills["bill_inv"] == inv][["container", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]]
            st.write(f"**{inv}** ({count} rows): ${dup_rows['amount'].sum():,.0f}")
            st.dataframe(dup_rows, use_container_width=True, hide_index=True)

# Missing vendor
if "non_inventory_vendor" in bills.columns:
    missing_vendor = bills["non_inventory_vendor"].isna().sum()
    if missing_vendor > 0:
        anomalies_found = True
        st.warning(f"**{missing_vendor:,} bills with missing vendor data**")

# Stale bills (pre-2024)
if date_col:
    stale_count = (bills[date_col] < pd.Timestamp("2024-01-01")).sum()
    if stale_count > 0:
        anomalies_found = True
        st.info(f"**{stale_count:,} bills from before 2024** (included in analysis)")

if not anomalies_found:
    st.success("No major data quality issues detected in cleaned dataset")

# =============================================================================
# SECTION 9: DOWNLOAD CLEANED DATA
# =============================================================================
st.header("9. Export Cleaned Dataset")

if st.button("Download Cleaned Bills as CSV"):
    csv = bills.to_csv(index=False)
    st.download_button(
        label="Download CSV",
        data=csv,
        file_name=f"bills_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

st.divider()
st.caption(
    "Bills Insights Dashboard | Cleaned dataset: "
    f"{cleaned_rows:,} rows ({retention_pct:.1f}% of original {original_rows:,}) | "
    f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
