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
from utils import load_html_table, clean_bills_dataframe

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
    """Load Bills.xls and perform thorough cleaning (shared logic in utils.py)."""
    bills_raw = load_html_table(Path("Bills.xls"))
    bills, original_count, final_count = clean_bills_dataframe(bills_raw)
    retention = 100 * final_count / original_count
    return bills, original_count, final_count, retention


bills, original_rows, cleaned_rows, retention_pct = load_and_clean_bills()

# Debug: check what we got
if bills is None or len(bills) == 0:
    st.error("Error: No data loaded. Check Bills.xls file.")
    st.stop()

# Ensure container column exists
if 'container' not in bills.columns:
    st.error(f"Error: 'container' column not found. Available columns: {list(bills.columns)}")
    st.stop()

# =============================================================================
# TOP SUMMARY
# =============================================================================
st.markdown("### What This Data Represents")

# Safely get counts
try:
    total_containers = bills['container'].nunique()
    if pd.isna(total_containers):
        total_containers = 0
    else:
        total_containers = int(total_containers)
except:
    total_containers = 0

try:
    total_amount = float(bills['amount'].sum())
except:
    total_amount = 0

st.markdown(
    f"""
    This dashboard analyzes **{cleaned_rows:,} freight and logistics bills**
    covering **{total_containers:,} different shipments** worth **${total_amount:,.0f}** total.

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

# Safely calculate metrics
total_billed = bills['amount'].sum() if 'amount' in bills.columns else 0
avg_bill = bills['amount'].mean() if 'amount' in bills.columns else 0
still_owed = bills['balance_due'].sum() if 'balance_due' in bills.columns else 0
pct_owed = (100 * still_owed / total_billed) if total_billed > 0 and 'balance_due' in bills.columns else 0

with col1:
    st.metric(
        "Total Billed",
        f"${total_billed:,.0f}",
        help="Total amount of all bills"
    )
with col2:
    st.metric(
        "Average Bill",
        f"${avg_bill:,.0f}",
        help="Average cost per bill"
    )
with col3:
    st.metric(
        "Still Owed",
        f"${still_owed:,.0f}",
        help="Amount still unpaid to vendors"
    )
with col4:
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

vendor_col = "non_inventory_vendor" if "non_inventory_vendor" in bills.columns else None

with col1:
    st.markdown("**Top Vendors by Number of Bills**")
    if vendor_col and vendor_col in bills.columns:
        top_vendors_count = bills[vendor_col].value_counts().head(10)
    else:
        top_vendors_count = pd.Series()

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
    if vendor_col and vendor_col in bills.columns and 'amount' in bills.columns:
        top_vendors_amount = bills.groupby(vendor_col)["amount"].sum().nlargest(10)
    else:
        top_vendors_amount = pd.Series()

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

# Safely count pending and junk bills
pending = bills["is_pending_literal"].sum() if "is_pending_literal" in bills.columns else 0
pending_amount = bills[bills["is_pending_literal"]]["amount"].sum() if "is_pending_literal" in bills.columns and "amount" in bills.columns else 0

junk = bills["is_placeholder_junk"].sum() if "is_placeholder_junk" in bills.columns else 0
junk_amount = bills[bills["is_placeholder_junk"]]["amount"].sum() if "is_placeholder_junk" in bills.columns and "amount" in bills.columns else 0

with col1:
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

air_count = bills["is_air_freight"].sum() if "is_air_freight" in bills.columns else 0
air_amount = bills[bills["is_air_freight"]]["amount"].sum() if "is_air_freight" in bills.columns and "amount" in bills.columns else 0

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

if 'container' in bills.columns:
    unique_containers = bills['container'].nunique()
    avg_bills_per_container = bills['container'].value_counts().mean() if len(bills) > 0 else 0
else:
    unique_containers = 0
    avg_bills_per_container = 0

st.markdown(
    f"""
    **{unique_containers:,} unique containers**
    **Average {avg_bills_per_container:.0f} bills per container**

    💡 Why multiple bills per container? Each container gets separate bills for:
    - Ocean freight (carrying the container across the ocean)
    - Customs clearance (getting it through customs)
    - Duty/taxes (government fees)
    - Drayage (local trucking to/from port)
    - Extras (storage, inspections, etc.)
    """
)

# Container prefix distribution
containerized = bills[~bills["is_air_freight"]].copy()
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
