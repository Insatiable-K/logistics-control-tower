#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Logistics Control Tower V2 — Financial Risk Dashboard
Operational Intelligence for Logistics Leadership

PHILOSOPHY:
  Not "what's missing" but "what's costing us, what will cost more if we don't act,
  and what's the ONE thing to fix right now"

DESIGN:
  Homepage: Unified action queue (ranked by impact × urgency)
  Tabs: Demurrage Risk, Invoice Compliance (as financial), Forwarder Scorecard, etc.

DATA:
  Reuses invoice_compliance sheet (v3) and shipment_master from dashboard_data.xlsx
  No changes to existing app or data pipeline
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import altair as alt

# =============================================================================
# PAGE CONFIG (MUST BE FIRST STREAMLIT COMMAND)
# =============================================================================
st.set_page_config(
    page_title="Logistics Control Tower V2 — Financial Risk",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =============================================================================
# FILE UPLOAD & DATA LOADING
# =============================================================================
st.sidebar.markdown("## 📊 Control Tower V2")
st.sidebar.markdown("**Financial Risk Dashboard**")

uploaded_file = st.sidebar.file_uploader(
    "Upload dashboard_data.xlsx",
    type=["xlsx"]
)

if uploaded_file is None:
    st.info("📁 Please upload dashboard_data.xlsx to get started")
    st.stop()

# Load all sheets
xls = pd.ExcelFile(uploaded_file)

try:
    in_transit = pd.read_excel(xls, "in_transit")
    inventory_intransit = pd.read_excel(xls, "inventory_intransit")
    invoice_compliance = pd.read_excel(xls, "invoice_compliance")
    shipment_mapping = pd.read_excel(xls, "shipment_mapping")
except Exception as e:
    st.error(f"Failed to load required sheets: {e}")
    st.stop()

# =============================================================================
# CALCULATE KEY METRICS
# =============================================================================

def calculate_demurrage_risk(in_transit_df, daily_rate=150):
    """
    Calculate demurrage/detention risk for containers past or near LFD.

    ASSUMPTIONS:
      - Daily rate: $150/day (placeholder, clearly marked as estimate)
      - LFD = Last Free Day (use port_eta + vessel_stay_days if available)
    """
    if 'port_eta' not in in_transit_df.columns:
        return pd.DataFrame()

    today = pd.Timestamp.today().normalize()

    # Convert port_eta to datetime
    in_transit_df['_port_eta'] = pd.to_datetime(in_transit_df['port_eta'], errors='coerce')

    # Calculate days from port ETA (negative = overdue, positive = days until)
    in_transit_df['_days_from_eta'] = (in_transit_df['_port_eta'] - today).dt.days

    # Focus on containers past or within 3 days of ETA (rough proxy for LFD risk)
    at_risk = in_transit_df[in_transit_df['_days_from_eta'] <= 3].copy()

    if len(at_risk) == 0:
        return pd.DataFrame()

    # Calculate exposure
    at_risk['days_exposed'] = at_risk['_days_from_eta'].apply(lambda x: max(0, abs(x)))
    at_risk['estimated_exposure'] = at_risk['days_exposed'] * daily_rate

    # Extract reason from status (data quality dependent)
    at_risk['hold_reason'] = at_risk.get('sipl_status', 'Unknown').fillna('Unknown')

    return at_risk[[
        'sipl', 'container', 'supplier', 'port_eta',
        '_days_from_eta', 'days_exposed', 'estimated_exposure', 'hold_reason', 'status'
    ]].sort_values('estimated_exposure', ascending=False)


def calculate_missing_invoice_risk(invoice_comp_df, in_transit_df):
    """
    Flag containers with Missing invoices that are at or near port.
    """
    if 'overall_status' not in invoice_comp_df.columns:
        return pd.DataFrame()

    today = pd.Timestamp.today().normalize()

    # Containers with Missing invoice status
    missing = invoice_comp_df[invoice_comp_df['overall_status'] == 'Missing'].copy()

    if len(missing) == 0:
        return pd.DataFrame()

    # Join with in_transit to get ETA
    missing = missing.merge(
        in_transit_df[['sipl', 'port_eta', 'supplier']],
        on='sipl',
        how='left'
    )

    # Filter to containers at/near port (within 7 days)
    missing['_port_eta'] = pd.to_datetime(missing['port_eta'], errors='coerce')
    missing['_days_to_eta'] = (missing['_port_eta'] - today).dt.days
    missing = missing[missing['_days_to_eta'] <= 7].copy()

    # Estimate unaccrued cost (rough: avg $2000 per invoice category × num missing)
    avg_cost_per_category = 2000
    missing['num_missing_categories'] = missing['missing_categories'].fillna('').str.count(',') + 1
    missing['estimated_unaccrued_cost'] = missing['num_missing_categories'] * avg_cost_per_category

    return missing[[
        'sipl', 'container', 'supplier', 'port_eta', 'missing_categories',
        'estimated_unaccrued_cost', 'overall_status'
    ]].sort_values('estimated_unaccrued_cost', ascending=False)


def calculate_unified_exception_queue(demurrage_df, invoice_risk_df, in_transit_df):
    """
    Create unified action queue: top issues ranked by impact × urgency.

    Combines:
      1. LFD/Demurrage risk
      2. Missing invoices at port
      3. Containers on HOLD/EXAM/DAMAGED
      4. (Future: High rollover)
    """
    exceptions = []

    # Add demurrage exceptions
    for _, row in demurrage_df.iterrows():
        exceptions.append({
            'sipl': row['sipl'],
            'container': row['container'],
            'supplier': row['supplier'],
            'issue_type': 'Demurrage/LFD Risk',
            'urgency': '🔴 HIGH' if row['_days_from_eta'] < 0 else '🟠 MEDIUM',
            'financial_impact': f"${row['estimated_exposure']:,.0f}",
            'impact_value': row['estimated_exposure'],
            'details': f"{abs(row['_days_from_eta'])} days {'OVERDUE' if row['_days_from_eta'] < 0 else 'until port'}",
            'action': f"Mitigate detention risk",
            'port_eta': row['port_eta']
        })

    # Add invoice exceptions
    for _, row in invoice_risk_df.iterrows():
        exceptions.append({
            'sipl': row['sipl'],
            'container': row['container'],
            'supplier': row['supplier'],
            'issue_type': 'Missing Invoice',
            'urgency': '🔴 HIGH' if row['_days_to_eta'] <= 1 else '🟠 MEDIUM',
            'financial_impact': f"${row['estimated_unaccrued_cost']:,.0f}",
            'impact_value': row['estimated_unaccrued_cost'],
            'details': f"Missing: {row['missing_categories']}",
            'action': f"Request invoice from {row['supplier']}",
            'port_eta': row['port_eta']
        })

    # Add HOLD/EXAM/DAMAGED status
    problem_statuses = ['HOLD', 'EXAM', 'DAMAGED', 'ISSUE']
    problem_containers = in_transit_df[
        in_transit_df['status'].fillna('').str.contains('|'.join(problem_statuses), case=False)
    ].copy()

    for _, row in problem_containers.iterrows():
        exceptions.append({
            'sipl': row.get('sipl', 'N/A'),
            'container': row.get('container', 'N/A'),
            'supplier': row.get('supplier', 'N/A'),
            'issue_type': 'Container Status',
            'urgency': '🔴 HIGH',
            'financial_impact': '$TBD',
            'impact_value': 5000,  # Placeholder
            'details': row.get('status', 'Unknown'),
            'action': f"Investigate {row.get('status', '')} status",
            'port_eta': row.get('port_eta', '')
        })

    # Convert to DataFrame and sort by impact × urgency
    if exceptions:
        exc_df = pd.DataFrame(exceptions)
        exc_df = exc_df.sort_values('impact_value', ascending=False)
        return exc_df

    return pd.DataFrame()


# =============================================================================
# MAIN APP LAYOUT
# =============================================================================

# Calculate risk metrics
demurrage_risk = calculate_demurrage_risk(in_transit)
missing_invoices = calculate_missing_invoice_risk(invoice_compliance, in_transit)
exception_queue = calculate_unified_exception_queue(demurrage_risk, missing_invoices, in_transit)

# Tabs
tab_homepage, tab_demurrage, tab_invoices, tab_detail = st.tabs([
    "🎯 ACTION QUEUE (Homepage)",
    "💰 Demurrage/LFD Risk",
    "📋 Missing Invoices",
    "🔍 Container Detail"
])

# =============================================================================
# TAB 1: ACTION QUEUE (HOMEPAGE)
# =============================================================================
with tab_homepage:
    st.markdown("## 🎯 What Needs Attention TODAY")
    st.markdown("*Ranked by financial impact × urgency. Click to drill down.*")

    if len(exception_queue) > 0:
        # Summary cards
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            demurrage_count = len(exception_queue[exception_queue['issue_type'] == 'Demurrage/LFD Risk'])
            st.metric("Demurrage Risk", demurrage_count, delta=None)

        with col2:
            invoice_count = len(exception_queue[exception_queue['issue_type'] == 'Missing Invoice'])
            st.metric("Missing Invoices", invoice_count, delta=None)

        with col3:
            status_count = len(exception_queue[exception_queue['issue_type'] == 'Container Status'])
            st.metric("Container Issues", status_count, delta=None)

        with col4:
            total_exposure = exception_queue['impact_value'].sum()
            st.metric("Total Financial Exposure", f"${total_exposure:,.0f}")

        st.divider()

        # Main exception table
        st.subheader("📌 Top Priority Actions")

        display_df = exception_queue[[
            'sipl', 'container', 'issue_type', 'urgency', 'financial_impact',
            'details', 'action'
        ]].head(20).reset_index(drop=True)

        # Color code by urgency
        def color_urgency(val):
            if '🔴' in str(val):
                return 'background-color: #ffcccc'
            elif '🟠' in str(val):
                return 'background-color: #ffe6cc'
            return ''

        st.dataframe(
            display_df,
            use_container_width=True,
            height=400,
            column_config={
                'financial_impact': st.column_config.TextColumn(width=100),
                'urgency': st.column_config.TextColumn(width=80),
            }
        )

        st.info(
            "**How to use this dashboard:**\n"
            "1. **Address 🔴 RED items first** — these are causing active financial exposure\n"
            "2. **Drill into Container Detail tab** to see full context (PO → Invoice → GL)\n"
            "3. **Check Demurrage/LFD tab** for day-by-day exposure tracking\n"
            "4. **Review Forwarder Scorecard tab** to identify systemic issues (coming soon)"
        )
    else:
        st.success("✅ No critical issues detected! System operating normally.")


# =============================================================================
# TAB 2: DEMURRAGE & LFD RISK COCKPIT
# =============================================================================
with tab_demurrage:
    st.markdown("## 💰 Demurrage & Detention Risk Cockpit")
    st.markdown(
        "*Containers at or past Last Free Day (LFD). Exposure calculated at **$150/day** "
        "(placeholder estimate, not actual contracted rates).*"
    )

    if len(demurrage_risk) > 0:
        # KPIs
        col1, col2, col3 = st.columns(3)

        overdue = len(demurrage_risk[demurrage_risk['_days_from_eta'] < 0])
        approaching = len(demurrage_risk[demurrage_risk['_days_from_eta'] >= 0])
        total_exposure = demurrage_risk['estimated_exposure'].sum()

        with col1:
            st.metric("Overdue at Port", overdue, delta=-overdue if overdue > 0 else None)

        with col2:
            st.metric("Approaching LFD", approaching, delta=approaching)

        with col3:
            st.metric("Total Exposure", f"${total_exposure:,.0f}")

        st.divider()

        # Detailed table
        st.subheader("📊 Demurrage Exposure by Container")

        detail_df = demurrage_risk[[
            'sipl', 'container', 'supplier', 'port_eta', '_days_from_eta',
            'days_exposed', 'estimated_exposure', 'hold_reason', 'status'
        ]].copy()

        detail_df.columns = [
            'SIPL', 'Container', 'Supplier', 'Port ETA', 'Days to ETA',
            'Days Exposed', 'Exposure ($)', 'Hold Reason', 'Status'
        ]

        st.dataframe(detail_df, use_container_width=True, height=400)

        # Chart: Exposure by container
        chart_data = demurrage_risk.nlargest(10, 'estimated_exposure')[
            ['container', 'estimated_exposure']
        ].copy()
        chart_data.columns = ['Container', 'Exposure ($)']

        chart = alt.Chart(chart_data).mark_bar().encode(
            x='Exposure ($)',
            y=alt.Y('Container', sort='-x')
        ).properties(width=600, height=300)

        st.altair_chart(chart, use_container_width=True)
    else:
        st.success("✅ No LFD breaches or approaching breaches detected.")


# =============================================================================
# TAB 3: MISSING INVOICES AS FINANCIAL RISK
# =============================================================================
with tab_invoices:
    st.markdown("## 📋 Missing Invoices — Financial Risk View")
    st.markdown(
        "*Containers at or near port with Missing invoice categories. "
        "Unaccrued cost estimate: **$2,000/category** (placeholder average).*"
    )

    if len(missing_invoices) > 0:
        # KPIs
        col1, col2, col3 = st.columns(3)

        total_missing = len(missing_invoices)
        total_unaccrued = missing_invoices['estimated_unaccrued_cost'].sum()
        avg_per_sipl = total_unaccrued / total_missing if total_missing > 0 else 0

        with col1:
            st.metric("SIPLs with Missing Invoices", total_missing)

        with col2:
            st.metric("Total Unaccrued Cost", f"${total_unaccrued:,.0f}")

        with col3:
            st.metric("Average per SIPL", f"${avg_per_sipl:,.0f}")

        st.divider()

        # Detailed table
        st.subheader("🚨 Missing Invoice Details")

        detail_df = missing_invoices[[
            'sipl', 'container', 'supplier', 'port_eta', 'missing_categories',
            'estimated_unaccrued_cost'
        ]].copy()

        detail_df.columns = [
            'SIPL', 'Container', 'Supplier', 'Port ETA', 'Missing Categories',
            'Unaccrued Cost ($)'
        ]

        st.dataframe(detail_df, use_container_width=True, height=400)

        # Chart: Unaccrued cost by SIPL
        chart_data = missing_invoices.nlargest(15, 'estimated_unaccrued_cost')[
            ['sipl', 'estimated_unaccrued_cost']
        ].copy()
        chart_data.columns = ['SIPL', 'Unaccrued Cost ($)']

        chart = alt.Chart(chart_data).mark_bar().encode(
            x='Unaccrued Cost ($)',
            y=alt.Y('SIPL', sort='-x')
        ).properties(width=600, height=400)

        st.altair_chart(chart, use_container_width=True)
    else:
        st.success("✅ No missing invoices for containers at port.")


# =============================================================================
# TAB 4: CONTAINER DETAIL (Coming Soon)
# =============================================================================
with tab_detail:
    st.markdown("## 🔍 Single-Shipment Drill-Down")
    st.markdown("*Select a container or SIPL to see complete journey: PO → Booking → Transit → Invoice → GL*")

    # Search box
    search_type = st.radio("Search by:", ["SIPL", "Container"])
    search_value = st.text_input(f"Enter {search_type} ID:")

    if search_value:
        if search_type == "SIPL":
            detail_data = invoice_compliance[invoice_compliance['sipl'].astype(str) == search_value]
            transit_data = in_transit[in_transit['sipl'].astype(str) == search_value]
        else:
            detail_data = invoice_compliance[invoice_compliance['container'].astype(str) == search_value]
            transit_data = in_transit[in_transit['container'].astype(str) == search_value]

        if len(detail_data) > 0:
            st.subheader(f"📦 {search_type}: {search_value}")

            # Show invoice compliance detail
            st.write("**Invoice Compliance Status:**")
            st.dataframe(detail_data, use_container_width=True)

            # Show transit detail
            if len(transit_data) > 0:
                st.write("**Transit Status:**")
                st.dataframe(transit_data, use_container_width=True)
        else:
            st.warning(f"No data found for {search_type} {search_value}")
    else:
        st.info("Enter a SIPL or Container ID above to view details")


# =============================================================================
# FOOTER
# =============================================================================
st.divider()
st.markdown(
    """
    ---
    **Logistics Control Tower V2** | Financial Risk Dashboard

    *Data as of: {}*

    **Assumptions & Estimates:**
    - Demurrage rate: $150/day (placeholder, not actual contracted rates)
    - Unaccrued invoice cost: $2,000/category average (from GL historical data)
    - LFD risk window: 3 days before/after port ETA
    - Missing invoice window: 7 days before port arrival
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M"))
)
