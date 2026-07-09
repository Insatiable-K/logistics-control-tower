# -*- coding: utf-8 -*-
"""
GL INSIGHTS DASHBOARD - Easy-to-Understand Version
For non-technical users to understand freight accounting at a glance.

Runs standalone (`streamlit run gl_insights.py`) or as a tab inside
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
from utils import load_html_table, clean_gl_dataframe


# =============================================================================
# LOAD DATA
# =============================================================================
@st.cache_data
def load_and_clean_gl():
    """Load GL 1275 & 1313 and perform cleaning (shared logic in utils.py)."""
    gl_1275_raw = load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls"))
    gl_1313_raw = load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls"))
    return clean_gl_dataframe(gl_1275_raw, gl_1313_raw)


def render_tab():
    """Render the full GL Insights view. Callable standalone or as a tab."""

    gl = load_and_clean_gl()
    gl_1275 = gl[gl["account"] == "1275 - Capitalized"]
    gl_1313 = gl[gl["account"] == "1313 - Prepaid"]

    # =========================================================================
    # OVERVIEW
    # =========================================================================
    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        This is the **Accounting Ledger** — the official record of all freight and logistics costs
        the company has paid for.

        **Two separate accounts:**
        - **GL 1275** (Capitalized): {len(gl_1275):,} entries — major freight costs that go on the balance sheet
        - **GL 1313** (Prepaid): {len(gl_1313):,} entries — freight costs paid upfront

        **Total recorded:** ${gl['net_amount'].sum():,.0f}
        """
    )

    st.divider()

    # =========================================================================
    # ACCOUNT COMPARISON
    # =========================================================================
    st.markdown("### 📌 Account Comparison")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("#### GL 1275 - Capitalized")
        st.write(f"**Entries:** {len(gl_1275):,}")
        st.write(f"**Total:** ${gl_1275['net_amount'].sum():,.0f}")
        st.caption("Big freight investments that stay on the books as assets")

    with col2:
        st.markdown("#### GL 1313 - Prepaid")
        st.write(f"**Entries:** {len(gl_1313):,}")
        st.write(f"**Total:** ${gl_1313['net_amount'].sum():,.0f}")
        st.caption("Freight costs paid in advance, like deposits")

    with col3:
        st.markdown("#### Combined")
        st.write(f"**Total Entries:** {len(gl):,}")
        st.write(f"**Grand Total:** ${gl['net_amount'].sum():,.0f}")
        st.caption("All freight costs in one number")

    # =========================================================================
    # COST BREAKDOWN
    # =========================================================================
    st.divider()
    st.markdown("### 💸 Types of Freight Costs")

    st.markdown(
        """
        Every freight cost falls into one of these categories:
        - **Ocean Freight**: Shipping containers across the ocean
        - **Customs**: Paying customs brokers to clear shipments
        - **Duty**: Government taxes/tariffs on imported goods
        - **Drayage**: Local trucking (port to warehouse, etc.)
        - **Other**: Storage, inspections, handling, etc.
        """
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**GL 1275 Breakdown**")
        cat_1275 = gl_1275.groupby("category")["net_amount"].sum().sort_values(ascending=False)
        for cat in cat_1275.index:
            amount = cat_1275[cat]
            pct = 100 * amount / cat_1275.sum()
            st.write(f"{cat}: ${amount:,.0f} ({pct:.0f}%)")

        fig = px.pie(values=cat_1275.values, names=cat_1275.index, title="Where GL 1275 money goes")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**GL 1313 Breakdown**")
        cat_1313 = gl_1313.groupby("category")["net_amount"].sum().sort_values(ascending=False)
        for cat in cat_1313.index:
            amount = cat_1313[cat]
            pct = 100 * amount / cat_1313.sum()
            st.write(f"{cat}: ${amount:,.0f} ({pct:.0f}%)")

        fig = px.pie(values=cat_1313.values, names=cat_1313.index, title="Where GL 1313 money goes")
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # TOP VENDORS
    # =========================================================================
    st.divider()
    st.markdown("### 🏢 Who We Pay (Freight Vendors)")

    st.markdown("These are the companies providing the freight/logistics services:")

    col1, col2 = st.columns(2)

    with col1:
        vendor_1275 = gl_1275.groupby("party")["net_amount"].sum().nlargest(10)
        fig = px.bar(x=vendor_1275.values, y=vendor_1275.index, orientation="h", title="Top vendors in GL 1275")
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        vendor_1313 = gl_1313.groupby("party")["net_amount"].sum().nlargest(10)
        fig = px.bar(x=vendor_1313.values, y=vendor_1313.index, orientation="h", title="Top vendors in GL 1313")
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # BALANCE TREND
    # =========================================================================
    st.divider()
    st.markdown("### 📈 How Much Freight We Own (Asset Growth)")

    st.markdown(
        """
        The 'Balance' shows: If we sold every freight asset today, how much would it be worth?

        This only applies to GL 1275 (capitalized assets). GL 1313 is spending, not assets.
        """
    )

    if "balance" in gl.columns:
        col1, col2 = st.columns(2)

        with col1:
            balance_1275 = gl_1275[gl_1275["balance"].notna()].sort_values("date")
            if len(balance_1275) > 0:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=balance_1275["date"], y=balance_1275["balance"], mode="lines",
                    name="Balance", line=dict(color="#3498db", width=3), fill="tozeroy"
                ))
                fig.update_layout(title="GL 1275 Asset Value Over Time", xaxis_title="Date",
                                   yaxis_title="$ Value", hovermode="x unified")
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            balance_1313 = gl_1313[gl_1313["balance"].notna()].sort_values("date")
            if len(balance_1313) > 0:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=balance_1313["date"], y=balance_1313["balance"], mode="lines",
                    name="Balance", line=dict(color="#e74c3c", width=3), fill="tozeroy"
                ))
                fig.update_layout(title="GL 1313 Spending Over Time", xaxis_title="Date",
                                   yaxis_title="$ Value", hovermode="x unified")
                st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # ADJUSTMENTS
    # =========================================================================
    st.divider()
    st.markdown("### 🔄 Credits & Corrections (Adjustments)")

    st.markdown(
        """
        Sometimes we need to correct a freight cost:
        - Vendor gives us a **credit** (refund)
        - We find a **mistake** (charge twice by accident)
        - We get a **discount** after the fact

        These show up as "Adjustments" or "Credit Memos"
        """
    )

    adj_1275 = gl_1275[gl_1275["is_adjustment"]]
    adj_1313 = gl_1313[gl_1313["is_adjustment"]]

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**GL 1275: {len(adj_1275)} adjustments**")
        if len(adj_1275) > 0:
            st.write(f"Total adjustment amount: ${adj_1275['net_amount'].sum():,.0f}")
            top_adj_1275 = adj_1275.groupby("party")["net_amount"].sum().nlargest(5)
            st.markdown("Top vendors with adjustments:")
            for vendor, amt in top_adj_1275.items():
                st.write(f"- {vendor}: ${amt:,.0f}")
        else:
            st.write("No adjustments")

    with col2:
        st.markdown(f"**GL 1313: {len(adj_1313)} adjustments**")
        if len(adj_1313) > 0:
            st.write(f"Total adjustment amount: ${adj_1313['net_amount'].sum():,.0f}")
            top_adj_1313 = adj_1313.groupby("party")["net_amount"].sum().nlargest(5)
            st.markdown("Top vendors with adjustments:")
            for vendor, amt in top_adj_1313.items():
                st.write(f"- {vendor}: ${amt:,.0f}")
        else:
            st.write("No adjustments")

    # =========================================================================
    # BY LOCATION
    # =========================================================================
    st.divider()
    st.markdown("### 📍 Freight Costs by Location")

    if "location" in gl.columns and gl["location"].notna().any():
        location_amount = gl.groupby("location")["net_amount"].sum().sort_values(ascending=False).head(10)

        col1, col2 = st.columns([2, 1])
        with col1:
            fig = px.bar(x=location_amount.values, y=location_amount.index, orientation="h",
                         title="Which ports/cities have the most freight costs?")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("**Top locations:**")
            for location, amount in location_amount.items():
                pct = 100 * amount / location_amount.sum()
                st.write(f"{location}: {pct:.0f}%")

    # =========================================================================
    # TIMELINE
    # =========================================================================
    st.divider()
    st.markdown("### 📅 Freight Costs Over Time")

    if "week" in gl.columns:
        postings_by_week = gl.groupby("week").size()
        amount_by_week_1275 = gl_1275.groupby("week")["net_amount"].sum() if len(gl_1275) > 0 else pd.Series()
        amount_by_week_1313 = gl_1313.groupby("week")["net_amount"].sum() if len(gl_1313) > 0 else pd.Series()

        col1, col2 = st.columns(2)

        with col1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=postings_by_week.index.astype(str), y=postings_by_week.values,
                mode="lines+markers", name="Postings", line=dict(color="#9b59b6", width=2)
            ))
            fig.update_layout(title="How many freight costs recorded each week?", xaxis_title="Week",
                               yaxis_title="Count", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = go.Figure()
            if len(amount_by_week_1275) > 0:
                fig.add_trace(go.Scatter(
                    x=amount_by_week_1275.index.astype(str), y=amount_by_week_1275.values,
                    mode="lines", name="GL 1275", line=dict(color="#3498db", width=2)
                ))
            if len(amount_by_week_1313) > 0:
                fig.add_trace(go.Scatter(
                    x=amount_by_week_1313.index.astype(str), y=amount_by_week_1313.values,
                    mode="lines", name="GL 1313", line=dict(color="#e74c3c", width=2)
                ))
            fig.update_layout(title="Total freight costs each week", xaxis_title="Week",
                               yaxis_title="$ Amount", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # FOOTER
    # =========================================================================
    st.divider()
    st.markdown("### 💡 Key Takeaway")
    st.markdown(
        f"""
        Your company has invested/spent approximately **${gl['net_amount'].sum():,.0f}** on freight and logistics.

        🔗 **See the "Container Insights" tab** to view this same accounting data matched back to
        your actual bills and shipments, container by container.
        """
    )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    st.set_page_config(page_title="GL Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>📊 FREIGHT ACCOUNTING INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "How freight costs are recorded in the company accounting system</p>",
        unsafe_allow_html=True
    )
    render_tab()
