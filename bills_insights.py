# -*- coding: utf-8 -*-
"""
BILLS INSIGHTS DASHBOARD - Easy-to-Understand Version
For non-technical users to understand invoice/bill patterns at a glance.
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
    load_html_table, standardize_columns, clean_container, clean_currency,
    clean_date, clean_text, is_pending
)

st.set_page_config(page_title="Bills Insights", layout="wide")

st.markdown(
    "<h1 style='text-align: center; color: #2c3e50;'>📊 BILLS INSIGHTS</h1>"
    "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
    "Understand your freight and logistics bills at a glance</p>",
    unsafe_allow_html=True
)

# =============================================================================
# LOAD & CLEAN DATA
# =============================================================================
@st.cache_data
def load_and_clean_bills():
    """Load Bills.xls and perform thorough cleaning."""
    bills_raw = load_html_table(Path("Bills.xls"))
    original_count = len(bills_raw)
    bills = standardize_columns(bills_raw)

    # Step 1: Remove rows with no container
    bills = bills[bills["container"].notna() & (bills["container"].astype(str).str.strip() != "")]

    # Step 2: Remove rows with no invoice number
    bills = bills[bills["bill_inv"].notna() & (bills["bill_inv"].astype(str).str.strip() != "")]

    # Step 3: Remove duplicate bills
    bills = bills.drop_duplicates(subset=["container", "bill_inv"], keep="first")

    # Step 4: Clean container
    bills["container_clean"] = bills["container"].apply(lambda x: clean_container(x, keep_air_freight_marker=True))
    bills = bills[bills["container_clean"].notna()]

    # Step 5: Clean amounts
    for col in ["amount", "balance_due", "sipl_amount"]:
        if col in bills.columns:
            bills[col] = clean_currency(bills[col])

    # Step 6: Clean dates
    for col in bills.columns:
        if "date" in col.lower() or "dt" in col.lower():
            if bills[col].dtype == "object":
                bills[col] = clean_date(bills[col])

    # Step 7: Flag anomalies (but keep them)
    bills["has_zero_amount"] = (bills["amount"] == 0) | bills["amount"].isna()
    bills["is_pending_literal"] = is_pending(bills["bill_inv"])
    bills["is_placeholder_junk"] = bills["bill_inv"].astype(str).apply(
        lambda x: str(x).strip().upper() in ["XENDING", "1", "2", "12", "POSTED", "DUPLICATE"]
    )
    bills["is_air_freight"] = bills["container_clean"] == "AIR FREIGHT"

    bills = bills.rename(columns={"container_clean": "container"})
    final_count = len(bills)
    retention = 100 * final_count / original_count

    return bills, original_count, final_count, retention


bills, original_rows, cleaned_rows, retention_pct = load_and_clean_bills()

# =============================================================================
# TOP SUMMARY
# =============================================================================
st.markdown("### What This Data Represents")
st.markdown(
    f"""
    This dashboard analyzes **{cleaned_rows:,} freight and logistics bills**
    covering **{bills['container'].nunique():,} different shipments** worth **${bills['amount'].sum():,.0f}** total.

    We cleaned the data by removing incomplete records, keeping only bills that have:
    - ✓ A shipment container number
    - ✓ An invoice number
    - ✓ A dollar amount

    This kept **{retention_pct:.0f}%** of the original data as usable.
    """
)

# =============================================================================
# KEY METRICS
# =============================================================================
st.divider()
st.markdown("### 💰 Money Summary")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "Total Billed",
        f"${bills['amount'].sum():,.0f}",
        help="Total amount of all bills"
    )
with col2:
    st.metric(
        "Average Bill",
        f"${bills['amount'].mean():,.0f}",
        help="Average cost per bill"
    )
with col3:
    st.metric(
        "Still Owed",
        f"${bills['balance_due'].sum():,.0f}" if "balance_due" in bills.columns else "N/A",
        help="Amount still unpaid to vendors"
    )
with col4:
    pct_owed = 100 * bills['balance_due'].sum() / bills['amount'].sum() if "balance_due" in bills.columns else 0
    st.metric(
        "% Unpaid",
        f"{pct_owed:.0f}%",
        help="Percentage of total bills still unpaid"
    )

# =============================================================================
# VENDOR SECTION
# =============================================================================
st.divider()
st.markdown("### 🏢 Who We Pay (Vendors)")

st.markdown(
    "**Pareto Principle**: Usually 80% of your spending goes to 20% of vendors. "
    "See who your top vendors are and how much you're spending with each."
)

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Top Vendors by Number of Bills**")
    top_vendors_count = bills["non_inventory_vendor"].value_counts().head(10) if "non_inventory_vendor" in bills.columns else pd.Series()
    if len(top_vendors_count) > 0:
        fig = px.bar(
            x=top_vendors_count.values,
            y=top_vendors_count.index,
            orientation="h",
            title="How many bills from each vendor",
            labels={"x": "Number of Bills", "y": "Vendor Name"}
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("📌 Read this as: How many separate invoices/bills we got from each company")

with col2:
    st.markdown("**Top Vendors by Total Money Spent**")
    top_vendors_amount = bills.groupby("non_inventory_vendor")["amount"].sum().nlargest(10) if "non_inventory_vendor" in bills.columns else pd.Series()
    if len(top_vendors_amount) > 0:
        fig = px.bar(
            x=top_vendors_amount.values,
            y=top_vendors_amount.index,
            orientation="h",
            title="How much money to each vendor",
            labels={"x": "Total $ Spent", "y": "Vendor Name"}
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("📌 Read this as: Total money we paid to each company. Notice it might be different from the bill count!")

# =============================================================================
# PROBLEMS & ISSUES
# =============================================================================
st.divider()
st.markdown("### ⚠️ Issues to Review")

col1, col2 = st.columns(2)

with col1:
    pending = bills["is_pending_literal"].sum()
    pending_amount = bills[bills["is_pending_literal"]]["amount"].sum()
    st.markdown(f"### 🟡 Waiting for Invoices")
    st.markdown(
        f"""
        **{pending} bills marked as "PENDING"** (${pending_amount:,.0f})

        Meaning: We know we need to pay these, but the actual invoice from the vendor hasn't arrived yet.
        This is money we definitely owe but are waiting to receive proper documentation for.
        """
    )
    if pending > 0:
        with st.expander("See pending bills"):
            pending_df = bills[bills["is_pending_literal"]][
                ["container", "bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]
            ].head(20)
            st.dataframe(pending_df, use_container_width=True, hide_index=True)

with col2:
    junk = bills["is_placeholder_junk"].sum()
    junk_amount = bills[bills["is_placeholder_junk"]]["amount"].sum()
    st.markdown(f"### 🔴 Suspicious Invoice Numbers")
    st.markdown(
        f"""
        **{junk} bills with placeholder invoice numbers** (${junk_amount:,.0f})

        Meaning: Instead of a real invoice number like "INV-2026-001", these show values like
        "XENDING" or just "1" or "2". This usually means data entry errors or temporary placeholders.
        These need investigation.
        """
    )
    if junk > 0:
        with st.expander("See suspicious bills"):
            junk_df = bills[bills["is_placeholder_junk"]][
                ["container", "bill_inv", "amount", "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else "supplier"]
            ].head(20)
            st.dataframe(junk_df, use_container_width=True, hide_index=True)

# Zero amounts
zero = bills["has_zero_amount"].sum()
if zero > 0:
    st.markdown(f"### 🔵 Zero-Dollar Bills ({zero} found)")
    st.markdown("Bills with $0 amount. Usually means cancelled or placeholder entries.")

# =============================================================================
# AIR FREIGHT
# =============================================================================
st.divider()
st.markdown("### ✈️ Air Freight (Shipments Without Containers)")

air_count = bills["is_air_freight"].sum()
air_amount = bills[bills["is_air_freight"]]["amount"].sum()

st.markdown(
    f"""
    **{air_count} air freight shipments** (${air_amount:,.0f})

    💡 Context: Shipping by air doesn't use containers. These shipments are tracked differently.
    They should be analyzed separately from container-based ocean/truck shipments.
    """
)

if air_count > 0 and "non_inventory_vendor" in bills.columns:
    air_vendor = bills[bills["is_air_freight"]]["non_inventory_vendor"].value_counts().head(5)
    st.markdown("**Top air freight vendors:**")
    for vendor, count in air_vendor.items():
        st.write(f"- {vendor}: {count} shipments")

# =============================================================================
# CONTAINER PATTERNS
# =============================================================================
st.divider()
st.markdown("### 📦 Container Patterns")

st.markdown(
    f"""
    **{bills['container'].nunique():,} unique containers**
    **Average {bills['container'].value_counts().mean():.0f} bills per container**

    💡 Why multiple bills per container? Each container gets separate bills for:
    - Ocean freight (carrying the container across the ocean)
    - Customs clearance (getting it through customs)
    - Duty/taxes (government fees)
    - Drayage (local trucking to/from port)
    - Extras (storage, inspections, etc.)
    """
)

# Container prefix distribution
containerized = bills[~bills["is_air_freight"]]
containerized["prefix"] = containerized["container"].str[:4]
prefix_dist = containerized["prefix"].value_counts().head(15)

fig = px.bar(
    x=prefix_dist.values,
    y=prefix_dist.index,
    orientation="h",
    title="Which shipping companies own the containers we use?",
    labels={"x": "How many containers", "y": "Company Code"}
)
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "📌 Container codes (like MEDU, MSMU) identify the company that owns/leases the container. "
    "Different companies may have different costs and availability."
)

# =============================================================================
# TIMELINE
# =============================================================================
st.divider()
st.markdown("### 📅 Timeline & Trends")

date_col = None
for col in ["invoice_dt", "created_date", "date"]:
    if col in bills.columns and bills[col].dtype == "datetime64[ns]":
        date_col = col
        break

if date_col:
    bills["week"] = bills[date_col].dt.to_period("W")
    bills_by_week = bills.groupby("week").size()
    amount_by_week = bills.groupby("week")["amount"].sum()

    col1, col2 = st.columns(2)

    with col1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=bills_by_week.index.astype(str),
            y=bills_by_week.values,
            mode="lines+markers",
            name="Bills",
            line=dict(color="#3498db", width=2)
        ))
        fig.update_layout(
            title="How many bills each week?",
            xaxis_title="Week",
            yaxis_title="Number of Bills",
            height=350,
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=amount_by_week.index.astype(str),
            y=amount_by_week.values,
            fill="tozeroy",
            name="Amount",
            line=dict(color="#27ae60", width=2)
        ))
        fig.update_layout(
            title="How much money in bills each week?",
            xaxis_title="Week",
            yaxis_title="$ Amount",
            height=350,
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)

    # Payment terms
    if "due_date" in bills.columns:
        bills["days_to_due"] = (bills["due_date"] - bills[date_col]).dt.days
        bills["days_to_due"] = bills["days_to_due"].clip(-30, 120)

        st.markdown("### Payment Terms")
        st.markdown(
            f"""
            **Average: {bills['days_to_due'].mean():.0f} days** from invoice to payment due

            💡 This tells you how long vendors typically give you to pay.
            Standard is 30 days, but some might be 15 or 60 days.
            """
        )

        fig = px.histogram(
            bills,
            x="days_to_due",
            nbins=25,
            title="Distribution of Payment Terms",
            labels={"days_to_due": "Days to Payment Due", "count": "Number of Bills"}
        )
        st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# EXPORT
# =============================================================================
st.divider()
st.markdown("### 📥 Download This Data")

if st.button("📊 Download Cleaned Bills as CSV"):
    csv = bills.to_csv(index=False)
    st.download_button(
        label="Download CSV File",
        data=csv,
        file_name=f"bills_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

st.caption(
    f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Data: {cleaned_rows:,} bills | Retention: {retention_pct:.0f}%"
)
