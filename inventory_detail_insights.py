# -*- coding: utf-8 -*-
"""
INVENTORY IN TRANSIT INSIGHTS DASHBOARD - Easy-to-Understand Version
The product/value detail: what's actually inside every shipment, and what
it's worth.

Runs standalone (`streamlit run inventory_detail_insights.py`) or as a tab
inside logistics_dashboard.py (which imports render_tab()).
"""

import streamlit as st
from datetime import datetime
import plotly.express as px
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_html_table, clean_in_transit_dataframe, clean_inventory_detail_dataframe,
    filter_inventory_to_tracked_sipls, standardize_columns, clean_currency
)


@st.cache_data
def load_sipl_universe():
    """Load the SIPL tracking list — the authoritative 'currently active' shipment universe."""
    raw = load_html_table(Path("In-Transit List by SIPL.xls"))
    df, _, _ = clean_in_transit_dataframe(raw)
    return df


@st.cache_data
def load_and_clean_inventory():
    """
    Load Inventory In Transit - Detail.xls, clean it, and drop any rows for
    SIPLs that don't appear on the tracking list (shared logic in utils.py).
    A SIPL missing from the tracking list has fallen off the "currently
    active" universe (already completed, or a stale export) and shouldn't
    count in Inventory Detail's own totals either.
    """
    raw = load_html_table(Path("Inventory In Transit - Detail .xls"))
    df, original_count, after_container_filter = clean_inventory_detail_dataframe(raw)

    # Pre-filter total value, computed live (not hardcoded) so the "how much
    # value did we lose by dropping domestic/parcel rows" comparison below
    # stays accurate if the source export changes. clean_currency() is a
    # shared low-level primitive, not a duplication of the cleaning pipeline.
    original_total_value = clean_currency(standardize_columns(raw)["total_cost"]).sum()

    sipl_universe = load_sipl_universe()
    df, _, final_count = filter_inventory_to_tracked_sipls(df, sipl_universe)

    return df, original_count, after_container_filter, final_count, original_total_value


def render_tab():
    """Render the full Inventory In Transit Insights view. Callable standalone or as a tab."""

    inv_df, original_rows, after_container_filter, cleaned_rows, original_total_value = load_and_clean_inventory()
    sipl_universe = load_sipl_universe()

    total_value = inv_df["total_cost"].sum()

    # =========================================================================
    # WHAT THIS SHOWS
    # =========================================================================
    st.markdown("### What This Data Represents")
    removed_domestic = original_rows - after_container_filter
    removed_untracked_sipl = after_container_filter - cleaned_rows
    value_removed_pct = 100 * (original_total_value - total_value) / original_total_value if original_total_value > 0 else 0
    st.markdown(
        f"""
        This is the **product and value detail** — what's actually inside every
        ocean-container shipment, broken down by product line, with quantities
        and dollar values.

        **Scoped to container freight, currently-tracked shipments only.** Of
        {original_rows:,} product lines in the raw export:
        - **{removed_domestic:,} ({100*removed_domestic/original_rows:.0f}%) had no ocean
          container** — mostly small parcel-shipped samples (UPS#..., FedEX#...) —
          removed, since this view tracks container logistics specifically.
        - **{removed_untracked_sipl:,}** more were for a SIPL no longer on the
          shipment tracking list — removed, since they're not part of the
          currently-active universe (a SIPL only shows up as untracked once it's
          already fully processed and dropped from tracking, or from a stale
          export snapshot).

        Together, those removed rows represented only **{value_removed_pct:.1f}%
        of total dollar value**, confirming it was mostly low-value noise, not
        meaningful freight.

        **{cleaned_rows:,} product line items**, covering
        **{inv_df['sipl'].nunique():,} SIPL bookings** on
        **{inv_df['container'].nunique():,} physical containers**.

        ℹ️ Note: unlike the other tabs, this view stays at the **product line
        item** level throughout — a single container legitimately carries many
        different products (avg {len(inv_df)/inv_df['container'].nunique():.1f}
        line items per container), so "what's inside" is inherently a
        line-item question, not a container-count one. Dollar totals below are
        summed and don't depend on grain either way.
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
    st.markdown("### ⚖️ Count vs. Value by Product Type")

    by_count = inv_df["type"].value_counts()
    by_value = inv_df.groupby("type")["total_cost"].sum().sort_values(ascending=False)

    # Within container-tracked freight, "Quartz Samples" is the type where
    # count and value diverge most sharply (unlike generic "Sample", which
    # barely appears here at all once domestic/parcel rows are excluded).
    top_count_type = by_count.index[0]
    qs_count_pct = 100 * by_count.get("Quartz Samples", 0) / len(inv_df)
    qs_value_pct = 100 * by_value.get("Quartz Samples", 0) / total_value if total_value > 0 else 0

    st.warning(
        f"""
        **"Quartz Samples" make up {qs_count_pct:.0f}% of container-tracked rows,
        but only {qs_value_pct:.2f}% of total dollar value.** Meanwhile
        **{top_count_type}** dominates both count and value — it's where the
        actual freight (and the actual money) is concentrated.

        Don't let sample-line volume distract from where the money actually is —
        see the value chart below.
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
    notes = [
        f"**{removed_domestic:,} row(s) removed** for having no ocean container "
        "(domestic/parcel, out of scope) or no SIPL on file at all (report footer artifacts)",
        f"**{removed_untracked_sipl:,} row(s) removed** for referencing a SIPL no "
        "longer on the shipment tracking list (already fully processed, or a stale export)",
    ]
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

    st.caption(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{inv_df['container'].nunique():,} containers | {cleaned_rows:,} line items | "
        f"${total_value:,.0f} total value"
    )


if __name__ == "__main__":
    st.set_page_config(page_title="Inventory In Transit Insights", layout="wide")
    st.markdown(
        "<h1 style='text-align: center; color: #2c3e50;'>📦 INVENTORY IN TRANSIT INSIGHTS</h1>"
        "<p style='text-align: center; color: #7f8c8d; font-size: 16px;'>"
        "What's inside every shipment, and what it's worth</p>",
        unsafe_allow_html=True
    )
    render_tab()
