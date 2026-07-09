# -*- coding: utf-8 -*-
"""
INVENTORY IN TRANSIT INSIGHTS DASHBOARD - Easy-to-Understand Version
The product/value detail: what's actually inside every shipment, and what
it's worth.

Runs standalone (`streamlit run inventory_detail_insights.py`) or as a tab
inside logistics_dashboard.py (which imports render_tab()).
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
from utils import load_html_table, clean_in_transit_dataframe, clean_inventory_detail_dataframe


@st.cache_data
def load_and_clean_inventory():
    """Load Inventory In Transit - Detail.xls and clean it (shared logic in utils.py)."""
    raw = load_html_table(Path("Inventory In Transit - Detail .xls"))
    df, original_count, final_count = clean_inventory_detail_dataframe(raw)
    return df, original_count, final_count


@st.cache_data
def load_sipl_universe():
    """Load the SIPL tracking list, used only for the cross-file coverage check."""
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    df, _, _ = clean_in_transit_dataframe(raw)
    return df


def render_tab():
    """Render the full Inventory In Transit Insights view. Callable standalone or as a tab."""

    inv_df, original_rows, cleaned_rows = load_and_clean_inventory()
    sipl_universe = load_sipl_universe()

    total_value = inv_df["total_cost"].sum()

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    st.markdown(
        f"""
        This is the **product and value detail** — what's actually inside every
        shipment, broken down by product line, with quantities and dollar values.

        **{cleaned_rows:,} product line items**, covering **{inv_df['sipl'].nunique():,} shipments**.
        """
    )

    # =========================================================================
    # HEADLINE KPI
    # =========================================================================
    st.divider()
    st.markdown("### 💰 Total Value Currently In Transit")

    st.metric("Inventory In Transit", f"${total_value:,.0f}")
    st.markdown(
        """
        💡 This is real working capital that's paid for but not yet landed —
        the single most important number this file produces for a finance
        or operations leader.
        """
    )

    # =========================================================================
    # COUNT VS VALUE BY PRODUCT TYPE — the lead insight
    # =========================================================================
    st.divider()
    st.markdown("### ⚖️ Count vs. Value: Samples Are Not What They Look Like")

    by_count = inv_df["type"].value_counts()
    by_value = inv_df.groupby("type")["total_cost"].sum().sort_values(ascending=False)

    sample_count_pct = 100 * by_count.get("Sample", 0) / len(inv_df)
    sample_value_pct = 100 * by_value.get("Sample", 0) / total_value if total_value > 0 else 0

    st.warning(
        f"""
        **"Sample" line items make up {sample_count_pct:.0f}% of all rows in this file,
        but only {sample_value_pct:.2f}% of total dollar value.**

        Don't let sample volume distract from where the money actually is — see
        the value chart below.
        """
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(x=by_count.values, y=by_count.index, orientation="h",
                     title="By Row Count", labels={"x": "# Line Items", "y": "Type"})
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig = px.bar(x=by_value.values, y=by_value.index, orientation="h",
                     title="By Dollar Value", labels={"x": "Total $", "y": "Type"})
        st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # CONTAINER-MODE BY TYPE — explains the "55% unresolved" honestly
    # =========================================================================
    st.divider()
    st.markdown("### 📦 Why Some Rows Show No Container")

    has_container = inv_df["has_real_container"].sum()
    no_container_pct = 100 * (1 - has_container / len(inv_df))

    st.markdown(
        f"""
        **{no_container_pct:.0f}% of rows don't resolve to a real ocean container ID.**
        This is NOT a data quality problem — it's two legitimately different
        shipping modes in one file. Full-size product (slabs, quartz) ships in
        ocean containers; small samples ship via parcel carriers (FedEx/UPS) that
        don't use container numbers at all.
        """
    )

    container_by_type = inv_df.groupby("type")["has_real_container"].agg(
        real_container="sum", total_rows="count"
    )
    container_by_type["pct_containerized"] = (
        100 * container_by_type["real_container"] / container_by_type["total_rows"]
    ).round(0)
    container_by_type = container_by_type.sort_values("pct_containerized", ascending=False)

    st.dataframe(
        container_by_type.rename(columns={
            "real_container": "Has Container", "total_rows": "Total Rows",
            "pct_containerized": "% Containerized"
        }),
        use_container_width=True
    )
    st.caption(
        "📌 Notice: SLAB, Quartz Slab, Porcelain Slab, and Quartz Samples are nearly "
        "100% containerized. Generic 'Sample' is the outlier at under 2% — confirming "
        "these ship a completely different way, by nature, not by accident."
    )

    # =========================================================================
    # PRODUCT TAXONOMY
    # =========================================================================
    st.divider()
    st.markdown("### 🏷️ Product Mix (Category)")

    cat_counts = inv_df["category"].value_counts().head(12)
    fig = px.pie(values=cat_counts.values, names=cat_counts.index, title="Product Category Mix (by row count)")
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Material sub-type detail (partial — only ~30% of rows have a subcategory on file)"):
        subcat = inv_df[inv_df["subcategory"].notna()]["subcategory"].value_counts().head(10)
        if len(subcat) > 0:
            st.dataframe(subcat.rename("Count"), use_container_width=True)
        else:
            st.write("No subcategory data available.")

    # =========================================================================
    # DISTRIBUTION NETWORK — where is value headed
    # =========================================================================
    st.divider()
    st.markdown("### 📍 Inventory Value by Destination")

    by_ship_to = inv_df.groupby("ship_to")["total_cost"].sum().sort_values(ascending=False).head(15)
    fig = px.bar(x=by_ship_to.values, y=by_ship_to.index, orientation="h",
                 title="Top 15 Destinations by Inventory Value ($)")
    st.plotly_chart(fig, use_container_width=True)

    # =========================================================================
    # CROSS-FILE COVERAGE GAP
    # =========================================================================
    st.divider()
    st.markdown("### 🔗 Coverage vs. the Shipment Tracking List")

    sipl_in_detail = set(inv_df["sipl"].astype(str).str.strip())
    sipl_in_tracking = set(sipl_universe["sipl"].astype(str).str.strip())
    tracked_no_detail = sipl_in_tracking - sipl_in_detail
    coverage_pct = 100 * len(sipl_in_detail & sipl_in_tracking) / len(sipl_in_tracking) if len(sipl_in_tracking) > 0 else 0

    st.markdown(
        f"""
        The Shipment Tracking tab tracks **{len(sipl_in_tracking):,} shipments**.
        Of those, **{len(sipl_in_tracking & sipl_in_detail):,} ({coverage_pct:.0f}%)**
        have product/value detail available here.

        **{len(tracked_no_detail):,} shipments ({100 - coverage_pct:.0f}%)** are tracked
        operationally (status, ETA) but have **no product/value breakdown yet** —
        likely just-initiated shipments where detail hasn't been entered, or a
        genuine process gap worth a quick check.
        """
    )

    # =========================================================================
    # DATA QUALITY
    # =========================================================================
    st.divider()
    st.markdown("### 🔍 Data Quality Notes")

    malformed = inv_df["category_is_malformed"].sum()
    notes = [f"**{original_rows - cleaned_rows} row(s) removed** during cleaning (no SIPL on file — includes report footer artifacts)"]
    if malformed > 0:
        notes.append(f"**{malformed} row(s)** have a malformed category value (numeric junk instead of a real category)")
    notes.append(
        "`subcategory` (blank on ~69% of rows) and `group` (blank on ~78%) are too "
        "sparse to be primary breakdown dimensions — shown as partial views only"
    )
    for n in notes:
        st.markdown(f"- {n}")

    # =========================================================================
    # EXPORT
    # =========================================================================
    st.divider()
    st.markdown("### 📥 Download This Data")

    if st.button("📊 Download Cleaned Inventory Detail as CSV", key="inventory_export_btn"):
        csv = inv_df.to_csv(index=False)
        st.download_button(
            label="Download CSV File", data=csv,
            file_name=f"inventory_detail_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", key="inventory_export_download"
        )

    st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {cleaned_rows:,} line items | ${total_value:,.0f} total value")


if __name__ == "__main__":
    st.set_page_config(page_title="Inventory In Transit Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>📦 INVENTORY IN TRANSIT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "What's inside every shipment, and what it's worth</p>",
        unsafe_allow_html=True
    )
    render_tab()
