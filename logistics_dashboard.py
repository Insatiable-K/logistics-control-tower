# -*- coding: utf-8 -*-
"""
LOGISTICS INSIGHTS DASHBOARD
Single entry point combining Bills, GL Accounting, Container, In-Transit,
Inventory, and Shipment Insights as tabs. Run with:
streamlit run logistics_dashboard.py

Each tab's content also runs standalone via its own file (bills_insights.py,
gl_insights.py, container_insights.py, in_transit_insights.py,
inventory_detail_insights.py, shipment_insights.py) if preferred.
"""

import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bills_insights
import gl_insights
import container_insights
import in_transit_insights
import inventory_detail_insights
import shipment_insights
import invoice_compliance_insights

st.set_page_config(page_title="Logistics Insights", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    "<h1 style='text-align: center; color: #2c3e50;'>🚢 LOGISTICS INSIGHTS DASHBOARD</h1>"
    "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
    "Bills, Accounting, Container, and Shipment-Level Views — all in one place</p>",
    unsafe_allow_html=True
)

tab_bills, tab_gl, tab_container, tab_in_transit, tab_inventory, tab_shipment, tab_compliance = st.tabs([
    "📊 Bills Insights",
    "📈 GL Accounting Insights",
    "📦 Container Insights",
    "🚦 In-Transit Insights",
    "🏷️ Inventory In Transit Insights",
    "🔗 Shipment Insights (Merged)",
    "📋 Invoice Compliance",
])

with tab_bills:
    bills_insights.render_tab()

with tab_gl:
    gl_insights.render_tab()

with tab_container:
    container_insights.render_tab()

with tab_in_transit:
    in_transit_insights.render_tab()

with tab_inventory:
    inventory_detail_insights.render_tab()

with tab_shipment:
    shipment_insights.render_tab()

with tab_compliance:
    invoice_compliance_insights.render_tab()
