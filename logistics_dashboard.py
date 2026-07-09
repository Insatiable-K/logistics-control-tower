# -*- coding: utf-8 -*-
"""
LOGISTICS INSIGHTS DASHBOARD
Single entry point combining Bills, GL Accounting, and Container Insights
as tabs. Run with: streamlit run logistics_dashboard.py

Each tab's content also runs standalone via its own file
(bills_insights.py, gl_insights.py, container_insights.py) if preferred.
"""

import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bills_insights
import gl_insights
import container_insights

st.set_page_config(page_title="Logistics Insights", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    "<h1 style='text-align: center; color: #2c3e50;'>🚢 LOGISTICS INSIGHTS DASHBOARD</h1>"
    "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
    "Bills, Accounting, and Container-Level Views — all in one place</p>",
    unsafe_allow_html=True
)

tab_bills, tab_gl, tab_container = st.tabs([
    "📊 Bills Insights",
    "📈 GL Accounting Insights",
    "📦 Container Insights",
])

with tab_bills:
    bills_insights.render_tab()

with tab_gl:
    gl_insights.render_tab()

with tab_container:
    container_insights.render_tab()
