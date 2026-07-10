import streamlit as st
import pandas as pd
from logic import (
    get_engine,
    load_bookings,
    build_execution_master,
    get_containers_on_water,
    get_arriving_today,
    get_next_7_days_arrivals,
    get_location_reached,
    get_location_next_7_days,
    get_port_eta_doc_risk,
    get_eta_performance,
    get_rollover_summary,
    get_container_data_issues,
    get_lfd_risk,
    build_invoice_compliance,
    get_arriving_invoice_risk,
)

# =============================================================================
# CONFIG (must be the first Streamlit command in the script)
# =============================================================================
st.set_page_config(layout="wide")

tab1, tab2, tab3 = st.tabs([
    "📦 Booking",
    "🚢 Container Movement",
    "🧾 Invoice Compliance"
])

with tab1:
    
    
    st.title("📦 Booking Performance Dashboard")
    
    # =============================================================================
    # LOAD DATA
    # =============================================================================
    engine = get_engine()
    bookings = load_bookings(engine)
    
    
    # =============================================================================
    # CLEAN DATA
    # =============================================================================
    bookings.columns = bookings.columns.str.lower().str.strip()
    
    bookings["event_date"] = pd.to_datetime(bookings["event_date"], errors="coerce")
    bookings["etd"] = pd.to_datetime(bookings["etd"], errors="coerce")
    
    # =============================================================================
    # DEFINE TRUE BOOKING LOGIC (BUSINESS RULE)
    # =============================================================================
    bookings["is_booking"] = (
        (bookings["event_status"] == "BOOKED") |
        (
            (bookings["event_status"] == "ROLLOVER") &
            (bookings["etd"].notna())
        )
    )
    
    # =============================================================================
    # SIDEBAR FILTERS (SAFE DATE HANDLING)
    # =============================================================================
    st.sidebar.header("🔍 Filters")
    
    date_range = st.sidebar.date_input(
        "Select Booking Date Range",
        value=(
            pd.Timestamp.today() - pd.Timedelta(days=7),
            pd.Timestamp.today()
        ),
        key="booking_date_range"
    )
    
    # -----------------------------------------------------------------------------
    # STRICT VALIDATION (RECOMMENDED)
    # -----------------------------------------------------------------------------
    if not (isinstance(date_range, tuple) and len(date_range) == 2):
        st.warning("⚠️ Please select both start and end date")
        st.stop()
    
    start_date, end_date = date_range
    
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)
    # FIX END DATE INCLUSIVITY
    end_date = end_date + pd.Timedelta(days=1)
    
    st.caption(
        f"Showing data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    )
    
    # =============================================================================
    # KPI CALCULATIONS
    # =============================================================================
    
    # -----------------------------
    # CLEAN BASE
    # -----------------------------
    df = bookings.copy()
    df = df[df["po_number"].notna()].copy()
    
    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    
    df["event_status"] = (
        df["event_status"]
        .astype(str)
        .str.upper()
        .str.strip()
    )
    

    # =============================================================================
    # KPI 1: BOOKINGS RECEIVED (FIRST OCCURRENCE)
    # =============================================================================
    
    po_first = (
        df.groupby("po_number", as_index=False)["event_date"]
        .min()
        .rename(columns={"event_date": "first_received_date"})
    )
    
    po_in_range = po_first[
        (po_first["first_received_date"] >= start_date) &
        (po_first["first_received_date"] < end_date)
    ]
    
    total_bookings = po_in_range["po_number"].nunique()
    
    # =============================================================================
    # KPI 2: BOOKINGS APPROVED (EVENT-BASED)
    # =============================================================================
    
    approved_events = df[
        (df["event_status"] != "HOLD") &
        (df["event_date"] >= start_date) &
        (df["event_date"] < end_date)
    ]
    
    total_approved = approved_events["po_number"].nunique()
    
    # =============================================================================
    # KPI 3: CONTAINERS CREATED (FIRST BOOKING EVENT)
    # =============================================================================
    
    is_booking = (
        (df["event_status"] == "BOOKED") |
        (df["event_status"] == "ROLLOVER")
    )
    
    first_container = (
        df[is_booking & df["container_id"].notna()]
        .groupby("container_id", as_index=False)["event_date"]
        .min()
    )
    
    container_in_range = first_container[
        (first_container["event_date"] >= start_date) &
        (first_container["event_date"] < end_date)
    ]
    
    total_containers = container_in_range["container_id"].nunique()
    
    # =============================================================================
    # KPI 4: BACKLOG (APPROVED BUT NOT CONTAINERIZED)
    # =============================================================================
    
    backlog = total_approved - total_containers
    
    # =============================================================================
    # KPI 5: APPROVED POs WITHOUT CONTAINER (CURRENT STATE)
    # =============================================================================
    
    approved_all = df[df["event_status"] != "HOLD"]
    
    latest_po_state = (
        approved_all.sort_values("event_date")
        .groupby("po_number", as_index=False)
        .last()
    )
    
    pos_without_container = latest_po_state[
        latest_po_state["container_id"].isna()
    ]
    
    missing_container_count = pos_without_container["po_number"].nunique()
    
    # =============================================================================
    # KPI 6: CONTAINERS SAILING (ETD BASED)
    # =============================================================================
    
    latest_etd_df = (
        df[df["etd"].notna()]
        .sort_values("event_date")
        .groupby("container_id", as_index=False)
        .last()
    )
    
    etd_filtered = latest_etd_df[
        (latest_etd_df["etd"] >= start_date) &
        (latest_etd_df["etd"] < end_date)
    ]
    
    containers_sailing_in_range = etd_filtered["container_id"].nunique()
    
    # =============================================================================
    # KPI 7: TODAY SAILING
    # =============================================================================
    
    today = pd.Timestamp.today()
    
    today_sailed = latest_etd_df[
        latest_etd_df["etd"].dt.normalize() == today.normalize()
    ]["container_id"].nunique()
    
    # =============================================================================
    # DATA TABLE
    # =============================================================================
    
    daily_events = df[
        (df["event_date"] >= start_date) &
        (df["event_date"] < end_date)
    ].copy()
    # -----------------------------
    # FORMAT DATE COLUMNS (DISPLAY ONLY)
    # -----------------------------
    date_cols = ["event_date", "etd", "eta"]
    
    for col in date_cols:
        if col in daily_events.columns:
            daily_events[col] = pd.to_datetime(daily_events[col], errors="coerce").astype(str).str[:10]
    
    # =============================================================================
    # KPI DISPLAY
    # =============================================================================
    
    st.header("📊 Booking KPIs")
    
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    col1.metric("📥 Bookings Received", total_bookings)
    col2.metric("✅ Bookings Approved", total_approved)
    col3.metric("📦 Containers Created", total_containers)
    col4.metric("📉 Booking Backlog", backlog)
    col5.metric("⚠️ Approved POs w/o Container", missing_container_count)
    col6.metric(
        "🚢 Containers Sailing",
        containers_sailing_in_range,
        delta=f"Today: {today_sailed}"
    )

    # =========================================================================
    # CANONICAL BOOKING KPI TABLE
    # Built once here from the dashboard variables above.
    # Reused directly by the MOM email — no separate calculation needed.
    # =========================================================================
    booking_kpi = pd.DataFrame({
        "KPI": [
            "Bookings Received",
            "Bookings Approved",
            "Containers Created",
            "Booking Backlog",
            "Approved POs Without Container",
            "Containers Sailing",
        ],
        "Count": [
            total_bookings,
            total_approved,
            total_containers,
            backlog,
            missing_container_count,
            containers_sailing_in_range,
        ],
    })

    # =============================================================================
    # DATA TABLE
    # =============================================================================
    st.subheader("📋 Booking Events")
    
    st.dataframe(
        daily_events.sort_values("event_date"),
        use_container_width=True
    )
    
    # =============================================================================
    # 📈 MONTHLY CONTAINER CREATION TREND (INDEPENDENT OF FILTERS)
    # =============================================================================
    import altair as alt
    
    st.subheader("📈 Monthly Container Creation Trend")
    
    # -----------------------------------------------------------------------------
    # STEP 1: FIRST BOOKING PER CONTAINER (LIFECYCLE START)
    # -----------------------------------------------------------------------------
    first_booking_trend = (
        bookings[bookings["is_booking"]]
        .sort_values("event_date")
        .groupby("container_id", as_index=False)
        .first()
    )
    
    # -----------------------------------------------------------------------------
    # STEP 2: CREATE MONTH (DISPLAY + SORT KEY)
    # -----------------------------------------------------------------------------
    first_booking_trend["month"] = first_booking_trend["event_date"].dt.strftime("%b %Y")
    first_booking_trend["month_sort"] = first_booking_trend["event_date"].dt.to_period("M")
    
    # -----------------------------------------------------------------------------
    # STEP 3: AGGREGATE (TRUE NEW CONTAINERS)
    # -----------------------------------------------------------------------------
    monthly_trend = (
        first_booking_trend
        .groupby(["month", "month_sort"])["container_id"]
        .nunique()
        .reset_index(name="new_containers")
        .sort_values("month_sort")
    )
    
    # -----------------------------------------------------------------------------
    # STEP 4: MOM GROWTH %
    # -----------------------------------------------------------------------------
    monthly_trend["mom_change_%"] = (
        monthly_trend["new_containers"].pct_change() * 100
    ).round(1)
    
    monthly_trend["mom_change_%"] = monthly_trend["mom_change_%"].fillna("")
    
    # -----------------------------------------------------------------------------
    # STEP 5: DISPLAY METRICS
    # -----------------------------------------------------------------------------
    col1, col2 = st.columns(2)
    
    total_containers_all_time = int(monthly_trend["new_containers"].sum())
    
    col1.metric(
        "Total Containers (All Time)",
        total_containers_all_time
    )
    
    if not monthly_trend.empty:
        latest_month = monthly_trend.iloc[-1]
    
        mom = latest_month["mom_change_%"]
    
        # Handle first month (no comparison)
        if mom == "" or pd.isna(mom):
            delta_text = "—"
        else:
            delta_text = f"{mom}%"
    
        col2.metric(
            f"Latest Month ({latest_month['month']})",
            int(latest_month["new_containers"]),
            delta=delta_text
        )
    else:
        col2.metric("Latest Month", 0)
    
    # -----------------------------------------------------------------------------
    # STEP 6: BAR CHART (FIXED + VISIBLE)
    # -----------------------------------------------------------------------------
    chart_df = monthly_trend.copy()
    chart_df = chart_df.sort_values("month_sort")
    
    # Ensure numeric
    chart_df["new_containers"] = pd.to_numeric(chart_df["new_containers"], errors="coerce")
    
    # Force correct order
    chart_df["month"] = pd.Categorical(
        chart_df["month"],
        categories=chart_df["month"],
        ordered=True
    )
    
    chart = alt.Chart(chart_df).mark_bar(size=40).encode(
        x=alt.X(
            "month:N",
            title="Month",
            sort=list(chart_df["month"])
        ),
        y=alt.Y(
            "new_containers:Q",
            title="Containers",
            scale=alt.Scale(domain=[0, chart_df["new_containers"].max() * 1.2])
        ),
        tooltip=[
            alt.Tooltip("month", title="Month"),
            alt.Tooltip("new_containers", title="Containers"),
            alt.Tooltip("mom_change_%", title="MoM %")
        ]
    )
    
    st.altair_chart(chart, use_container_width=True)
    
    # -----------------------------------------------------------------------------
    # STEP 7: OPTIONAL TABLE (FOR DEBUG / TRUST)
    # -----------------------------------------------------------------------------
    with st.expander("📊 Monthly Breakdown"):
        st.dataframe(
            monthly_trend.drop(columns=["month_sort"]),
            use_container_width=True
        )
        
        
        
    
    # =============================================================================
    # 🚨 UNMAPPED BOOKINGS (GLOBAL + MAPPING VALIDATED)
    # =============================================================================
    
    df_map = pd.read_sql("SELECT * FROM shipment_mapping", engine)
    df_map.columns = df_map.columns.str.lower().str.strip()
    
    
    # -----------------------------------------------------------------------------
    # FIX KEY TYPES
    # -----------------------------------------------------------------------------
    bookings["po_number"] = bookings["po_number"].astype(str).str.strip()
    df_map["po_number"] = df_map["po_number"].astype(str).str.strip()
    
    df_map.rename(columns={"container_id": "container_map"}, inplace=True)
    
    # -----------------------------------------------------------------------------
    # MERGE
    # -----------------------------------------------------------------------------
    df_check = bookings.merge(
        df_map[["po_number", "container_map"]],
        on="po_number",
        how="left"
    )
    
    # -----------------------------------------------------------------------------
    # TRUE UNMAPPED (NO DATE FILTER)
    # -----------------------------------------------------------------------------
    unmapped = df_check[
        df_check["is_booking"] &
        (
            df_check["container_id"].isna() &
            df_check["container_map"].isna()
        )
    ]
    
    # -----------------------------------------------------------------------------
    # REMOVE DUPLICATES (LATEST STATE PER PO)
    # -----------------------------------------------------------------------------
    unmapped_latest = (
        unmapped.sort_values("event_date")
        .groupby("po_number", as_index=False)
        .last()
    )
    
    unmapped_count = unmapped_latest["po_number"].nunique()
    
    unmapped_latest["days_since_booking"] = (
        pd.Timestamp.today() - unmapped_latest["event_date"]
    ).dt.days
    
    # -----------------------------------------------------------------------------
    # KPI
    # -----------------------------------------------------------------------------
    st.subheader("🚨 Booking Gaps (Current State)")
    
    st.metric(
        "⚠️ True Unmapped Bookings",
        unmapped_count,
        help="Bookings with no container assigned in both booking and mapping layer"
    )
    
    # -----------------------------------------------------------------------------
    # TABLE
    # -----------------------------------------------------------------------------
    st.subheader("📋 Current Unmapped Bookings")
    
    unmapped_display = unmapped_latest.copy()
    
    for col in ["event_date", "etd", "eta"]:
        if col in unmapped_display.columns:
            unmapped_display[col] = pd.to_datetime(unmapped_display[col], errors="coerce").astype(str).str[:10]
    
    st.dataframe(
        unmapped_display.sort_values("event_date", ascending=False),
        use_container_width=True
    )
        
    
    
    
    # =============================================================================
    # 🔁 HIGH ROLLOVER CONTAINERS (CORRECT LOGIC)
    # =============================================================================
    st.subheader("🔁 High Rollover Containers")
    
    rollover_summary = get_rollover_summary(bookings)
    
    high_rollover = rollover_summary[
        rollover_summary["rollover_count"] > 1
    ]
    
    st.metric(
        "Containers with Multiple Rollovers",
        high_rollover["container_id"].nunique()
    )
    
    st.dataframe(
        high_rollover.sort_values("rollover_count", ascending=False),
        use_container_width=True
    )
    
    # =============================================================================
    # 🔍 CONTAINER DRILLDOWN
    # =============================================================================
    st.subheader("🔍 Container Drilldown")
    
    container_list = sorted(bookings["container_id"].dropna().unique())
    
    search_container = st.text_input(
        "Enter Container Number",
        placeholder= ""
    )
    
    if search_container:
    
        search_container = search_container.strip().upper()
    
        if search_container in container_list:
    
            container_data = bookings[
                bookings["container_id"] == search_container
            ].sort_values("event_date")
    
            st.subheader(f"📦 Container Timeline: {search_container}")
    
            container_display = container_data.copy()
            
            for col in ["event_date", "etd", "eta"]:
                if col in container_display.columns:
                    container_display[col] = pd.to_datetime(container_display[col], errors="coerce").astype(str).str[:10]
            
            st.dataframe(
                container_display,
                use_container_width=True
            )
                
            # Optional quick insight
            rollover_count = (
                container_data["event_status"] == "ROLLOVER"
            ).sum()
    
            st.metric(
                "Total Rollovers",
                int(rollover_count)
            )
    
        else:
            st.warning("Container not found")
          
            
            
          
            
          

with tab2:

    st.title("🚢 Container Movement Control Tower")

    df_exec = build_execution_master(engine)

    # --------------------------------------------------
    # DATA INTEGRITY CHECK
    # --------------------------------------------------
    issues_df, container_check = get_container_data_issues(df_exec)

    # =============================================================================
    # 📊 STATUS (TOP PRIORITY — DECISION VIEW)
    # =============================================================================
    st.header("📊 Container Status")

    if "sipl_status" in df_exec.columns:

        status_df = df_exec.copy()

        status_df["sipl_status"] = (
            status_df["sipl_status"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

        status_summary = (
            status_df.groupby("sipl_status")
            .agg(container_count=("container_id", "nunique"))
            .reset_index()
            .sort_values("container_count", ascending=False)
        )

        total = status_summary["container_count"].sum()

        # 🔴 Bottleneck KPI
        #if not status_summary.empty:
            #top = status_summary.iloc[0]

            #st.metric(
               # f"📌 Bottleneck: {top['sipl_status']}",
                #top["container_count"],
               # delta=f"{round((top['container_count']/total)*100,1)}% of total"
           # )

        # 📊 Distribution Table (CLEAN + SIMPLE)
        
        status_summary = status_summary.rename(columns={
            "sipl_status": "Status",
            "container_count": "Containers"
        })
        
        # Optional: % with no decimals
        status_summary["%"] = (
            (status_summary["Containers"] / total) * 100
        ).round(0).astype(int)
        
        # 🔴 Highlight problematic statuses
        def highlight(row):
            val = str(row.get("Status", ""))
            if any(x in val for x in ["HOLD", "EXAM", "DAMAGED"]):
                return ["background-color: #5c1a1a"] * len(row)
            return [""] * len(row)
        
        # If you ONLY want Status + Containers → drop %
        show_cols = ["Status", "Containers", "%"]  # change to ["Status", "Containers", "%"] if needed
        
        st.dataframe(
            status_summary[show_cols]
            .style.apply(highlight, axis=1),
            use_container_width=True
        )

    # =============================================================================
    # 🚢 CURRENT MOVEMENT
    # =============================================================================
    st.header("🚢 Current Movement")

    col1, col2, col3 = st.columns(3)

    col1.metric("🚢 On Water", get_containers_on_water(df_exec))
    col2.metric("📍 Arriving at port Today", get_arriving_today(df_exec))
    col3.metric("🏢 Reaching Branch Today", get_location_reached(df_exec))

    # =============================================================================
    # 📅 UPCOMING
    # =============================================================================
    st.header("📅 Upcoming Movement (Next 7 Days)")

    col4, col5 = st.columns(2)

    col4.metric("📍 Port ETA", get_next_7_days_arrivals(df_exec))
    col5.metric("📦 Branch ETA", get_location_next_7_days(df_exec))

    # =============================================================================
    # 🚨 EXCEPTIONS
    # =============================================================================
    st.header("🚨 Exceptions")

    if "sipl_status" in df_exec.columns:

        exception_df = df_exec[
            df_exec["sipl_status"].str.upper().isin([
                "ON EXAM", "DAMAGED", "ON HOLD"
            ])
        ]

        st.metric(
            "Containers in Exception",
            exception_df["container_id"].nunique()
        )

        exception_display = exception_df.copy()

        for col in exception_display.columns:
            if pd.api.types.is_datetime64_any_dtype(exception_display[col]):
                exception_display[col] = exception_display[col].astype(str).str[:10]

        st.dataframe(exception_display, use_container_width=True)

    # =============================================================================
    # 🚨 LFD RISK
    # =============================================================================
    st.header("🚨 LFD Risk (Demurrage Exposure)")

    breach_df, approaching_df = get_lfd_risk(df_exec)

    st.markdown(f"""
    🔴 **LFD Breach:** {breach_df['container_id'].nunique()}  
    🟡 **Approaching (Next 3 Days):** {approaching_df['container_id'].nunique()}
    """)

    def format_dates(df):
        df = df.copy()
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].astype(str).str[:10]
        return df

    st.markdown("### 🔴 Already Past LFD")
    st.dataframe(format_dates(breach_df), use_container_width=True)

    st.markdown("### 🟡 Approaching LFD")
    st.dataframe(format_dates(approaching_df), use_container_width=True)

    # =============================================================================
    # 📋 DOCUMENTATION RISK
    # =============================================================================
    st.header("📋 Documentation Risk")
    
    risk_df = get_port_eta_doc_risk(df_exec)
    
    already_df = risk_df[risk_df["risk_type"] == "Already at Port"]
    upcoming_df = risk_df[risk_df["risk_type"] == "Upcoming"]
    
    
    # -----------------------------
    # 🔧 FORMAT DATES (REMOVE TIME)
    # -----------------------------
    def format_dates(df):
        df = df.copy()
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].astype(str).str[:10]
        return df
    
    
    already_display = format_dates(already_df)
    upcoming_display = format_dates(upcoming_df)
    
    
    # -----------------------------
    # DISPLAY
    # -----------------------------
    st.markdown(f"🔴 Already at Port: {already_df['container_id'].nunique()}")
    st.dataframe(already_display, use_container_width=True)
    
    st.markdown(f"🟡 Upcoming: {upcoming_df['container_id'].nunique()}")
    st.dataframe(upcoming_display, use_container_width=True)

    # =============================================================================
    # 🚨 DATA INTEGRITY
    # =============================================================================
    st.header("🚨 Container Data Integrity")

    st.metric(
        "Problem Containers",
        container_check[container_check["issue_type"] != ""]["container_id"].nunique()
    )

    problem_display = issues_df.copy()

    for col in problem_display.columns:
        if pd.api.types.is_datetime64_any_dtype(problem_display[col]):
            problem_display[col] = problem_display[col].astype(str).str[:10]

    problem_display = problem_display.loc[:, ~problem_display.columns.duplicated()]

    st.dataframe(problem_display, use_container_width=True)

    # =============================================================================
    # 📊 DELAY PERFORMANCE
    # =============================================================================
    st.header("📊 Delay Performance")

    eta_df = get_eta_performance(df_exec, bookings)

    delay_df = eta_df[
        eta_df["transit_delay"].notna() &
        (eta_df["transit_delay"] > 0)
    ]

    delay_df = (
        delay_df.sort_values("transit_delay", ascending=False)
        .groupby("container_id", as_index=False)
        .first()
    )

    st.metric("Delayed Containers", delay_df["container_id"].nunique())

    delay_display = delay_df.copy()

    for col in delay_display.columns:
        if pd.api.types.is_datetime64_any_dtype(delay_display[col]):
            delay_display[col] = delay_display[col].astype(str).str[:10]

    st.dataframe(delay_display, use_container_width=True)

    # =============================================================================
    # 🟠 FORWARDER PERFORMANCE
    # =============================================================================
    st.header("🟠 Forwarder Performance")

    if not delay_df.empty:

        forwarder_perf = (
            delay_df.groupby("forwarder")
            .agg(
                container_count=("container_id", "nunique"),
                avg_delay_days=("transit_delay", "mean")
            )
            .reset_index()
            .sort_values("avg_delay_days", ascending=False)
        )

        forwarder_perf["avg_delay_days"] = forwarder_perf["avg_delay_days"].round(1)

        st.dataframe(forwarder_perf, use_container_width=True)

    else:
        st.info("No delay data available")
        
with tab3:

    st.title("🧾 Invoice Compliance Dashboard")

    invoice_compliance = build_invoice_compliance(engine)

    invoice_risk = get_arriving_invoice_risk(
        df_exec,
        invoice_compliance,
        days=3
    )

    # ==========================================================
    # KPI
    # ==========================================================

    total_arriving = invoice_risk["container_id"].nunique()

    missing_mask = (
        invoice_risk["missing_bills"]
        .fillna("")
        .str.strip()
        .ne("")
    )

    pending_mask = (
        invoice_risk["pending_bills"]
        .fillna("")
        .str.strip()
        .ne("")
    )

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Containers Arriving (Next 3 Days)",
        total_arriving
    )

    col2.metric(
        "Containers Missing Bills",
        invoice_risk.loc[
            missing_mask,
            "container_id"
        ].nunique()
    )

    col3.metric(
        "Containers With Pending Bills",
        invoice_risk.loc[
            pending_mask,
            "container_id"
        ].nunique()
    )

    # ==========================================================
    # MISSING BILLS
    # ==========================================================

    st.subheader("🔴 Containers Missing Bills")

    missing_df = invoice_risk.loc[
        missing_mask,
        [
            "container_id",
            "po_number",
            "port_eta",
            "missing_bills"
        ]
    ].drop_duplicates()

    if not missing_df.empty:

        missing_df["port_eta"] = (
            pd.to_datetime(
                missing_df["port_eta"],
                errors="coerce"
            ).dt.strftime("%Y-%m-%d")
        )

        st.dataframe(
            missing_df.sort_values("port_eta"),
            use_container_width=True
        )

    else:

        st.success("No containers are missing required bills.")

    # ==========================================================
    # PENDING BILLS
    # ==========================================================

    st.subheader("🟡 Containers With Pending Bills")

    pending_df = invoice_risk.loc[
        pending_mask,
        [
            "container_id",
            "po_number",
            "port_eta",
            "pending_bills"
        ]
    ].drop_duplicates()

    if not pending_df.empty:

        pending_df["port_eta"] = (
            pd.to_datetime(
                pending_df["port_eta"],
                errors="coerce"
            ).dt.strftime("%Y-%m-%d")
        )

        st.dataframe(
            pending_df.sort_values("port_eta"),
            use_container_width=True
        )

    else:

        st.success("No pending placeholder bills.")
        
        
# =============================================================================
# MOM EMAIL SECTION FOR STREAMLIT APP
# =============================================================================

import win32com.client as win32
from datetime import datetime

st.header("📧 Daily Logistics MOM")

with st.expander("Generate MOM Email", expanded=False):

    recipients = st.text_input(
        "To",
        placeholder="name@company.com"
    )

    cc = st.text_input(
        "CC",
        placeholder="optional"
    )

    custom_note = st.text_area(
        "Additional Note",
        placeholder="Write operational notes..."
    )

    # =========================================================================
    # BOOKING KPI TABLE
    # Reused directly from the canonical table built above after the
    # dashboard KPI metrics. No separate calculation — always in sync.
    # =========================================================================

    # booking_kpi is already defined above — nothing to recalculate here.

    # =========================================================================
    # STATUS
    # =========================================================================

    status_summary = (
        df_exec.groupby("sipl_status")["container_id"]
        .nunique()
        .reset_index(name="Count")
        .sort_values("Count", ascending=False)
    )

    status_summary.columns = ["Status", "Count"]

    status_summary = status_summary.head(10)
    # =========================================================================
    # CURRENT MOVEMENT
    # =========================================================================

    current_movement = pd.DataFrame({

        "Metric": [
            "On Water",
            "Arriving at port Today",
            "Reaching Location Today"
        ],

        "Count": [
            get_containers_on_water(df_exec),
            get_arriving_today(df_exec),
            get_location_reached(df_exec)
        ]
    })

    # =========================================================================
    # UPCOMING
    # =========================================================================

    upcoming_movement = pd.DataFrame({

        "Metric": [
            "Port ETA (7D)",
            "Location ETA (7D)"
        ],

        "Count": [
            get_next_7_days_arrivals(df_exec),
            get_location_next_7_days(df_exec)
        ]
    })

    # =========================================================================
    # EXCEPTIONS
    # =========================================================================
    
    exception_df = df_exec.copy()
    
    exception_df["sipl_status"] = (
        exception_df["sipl_status"]
        .astype(str)
        .str.upper()
        .str.strip()
    )
    
    preferred_cols = [
        "container_id",
        "port_eta",
        "final_destination",
        "delivery_eta",
        "sipl_status"
    ]
    
    available_cols = [
        c for c in preferred_cols
        if c in exception_df.columns
    ]
    
    exception_email = exception_df[
        exception_df["sipl_status"].isin([
            "ON EXAM",
            "DAMAGED",
            "ON HOLD"
        ])
    ][available_cols].copy()
    
    rename_map = {
        "container_id": "Container",
        "port_eta": "Port",
        "final_destination": "Location",
        "delivery_eta": "Location ETA",
        "sipl_status": "Exception"
    }
    
    exception_email.rename(
        columns=rename_map,
        inplace=True
    )
    
    if "Location ETA" in exception_email.columns:
    
        exception_email["Location ETA"] = (
            pd.to_datetime(
                exception_email["Location ETA"],
                errors="coerce"
            )
            .astype(str)
            .str[:10]
        )
    
    exception_email = (
        exception_email
        .drop_duplicates()
        .head(15)
    )

   # =========================================================================
    # LFD RISK
    # =========================================================================
    
    breach_df, approaching_df = get_lfd_risk(df_exec)
    
    lfd_email = pd.concat([
        breach_df,
        approaching_df
    ])
    
    preferred_cols = [
        "container_id",
        "port_eta",
        "final_destination",
        "delivery_eta",
        "lfd",
        "sipl_status"
    ]
    
    available_cols = [
        c for c in preferred_cols
        if c in lfd_email.columns
    ]
    
    lfd_email = lfd_email[
        available_cols
    ].copy()
    
    rename_map = {
        "container_id": "Container",
        "port_eta": "Port",
        "final_destination": "Location",
        "delivery_eta": "Location ETA",         
        "lfd": "LFD Date",
        "sipl_status":"Status"
    
    }
    
    lfd_email.rename(
        columns=rename_map,
        inplace=True
    )
    
    # FORMAT DATE
    if "LFD Date" in lfd_email.columns:
    
        lfd_email["LFD Date"] = (
            pd.to_datetime(
                lfd_email["LFD Date"],
                errors="coerce"
            )
            .astype(str)
            .str[:10]
        )
    
    lfd_email = (
        lfd_email
        .drop_duplicates()
        .head(15)
    )
    # =========================================================================
    # DOCUMENT RISK
    # =========================================================================
    
    doc_risk = get_port_eta_doc_risk(df_exec)
    
    preferred_cols = [
        "container_id",
        "port_eta",
        "final_destination",
        "delivery_eta",
        "sipl_status"
    ]
    
    available_cols = [
        c for c in preferred_cols
        if c in doc_risk.columns
    ]
    
    doc_email = doc_risk[
        available_cols
    ].copy()
    
    rename_map = {
        "container_id": "Container",
        "port_eta": "Port",
        "final_destination": "Location",
        "delivery_eta": "Location ETA",
        "sipl_status": "Status"
    }
    
    doc_email.rename(
        columns=rename_map,
        inplace=True
    )
    
    if "Location ETA" in doc_email.columns:
    
        doc_email["Location ETA"] = (
            pd.to_datetime(
                doc_email["Location ETA"],
                errors="coerce"
            )
            .astype(str)
            .str[:10]
        )
    
    doc_email = (
        doc_email
        .drop_duplicates()
        .head(15)
    )
    # =========================================================================
    # DATA INTEGRITY
    # =========================================================================
    
    issues_df = get_container_data_issues(df_exec)[0]
    
    preferred_cols = [
        "container_id",
        "port_eta",
        "final_destination",
        "delivery_eta",
        "sipl_status",
        "issue_type"
    ]
    
    available_cols = [
        c for c in preferred_cols
        if c in issues_df.columns
    ]
    
    issues_email = issues_df[
        available_cols
    ].copy()
    
    rename_map = {
        "container_id": "Container",
        "port_eta": "Port eta",
        "final_destination": "Location",
        "delivery_eta": "Location ETA",
        "sipl_status": "SIPL Status",
        "issue_type": "Missing Data"
    }
    
    issues_email.rename(
        columns=rename_map,
        inplace=True
    )
    
    if "Location ETA" in issues_email.columns:
    
        issues_email["Location ETA"] = (
            pd.to_datetime(
                issues_email["Location ETA"],
                errors="coerce"
            )
            .astype(str)
            .str[:10]
        )
    
    issues_email = (
        issues_email
        .drop_duplicates()
        .head(15)
    )

    # =========================================================================
    # COMPACT HTML
    # =========================================================================

    html = f"""

    <html>

    <head>

    <style>

    body {{
        font-family: Calibri;
        font-size: 13px;
        color: #222;
        padding: 12px;
    }}

    h2 {{
        color: #0F4C81;
        margin-bottom: 5px;
    }}

    h3 {{
        background-color: #0F4C81;
        color: white;
        padding: 5px 8px;
        margin-top: 18px;
        margin-bottom: 5px;
        font-size: 14px;
    }}

    table {{
        border-collapse: collapse;
        width: 100%;
        margin-bottom: 12px;
    }}

    th {{
        background-color: #D9EAF7;
        border: 1px solid #CFCFCF;
        padding: 5px;
        text-align: left;
    }}

    td {{
        border: 1px solid #E3E3E3;
        padding: 5px;
    }}

    tr:nth-child(even) {{
        background-color: #F8FAFC;
    }}

    .summary {{
        background-color: #F4F8FB;
        padding: 8px;
        border-left: 4px solid #0F4C81;
        margin-bottom: 10px;
    }}

    .note {{
        background-color: #FFF8E8;
        padding: 8px;
        border-left: 4px solid #F0AD4E;
    }}

    </style>

    </head>

    <body>

    <h2>🚢 Daily Logistics MOM</h2>

    <div class='summary'>
    <b>Date:</b> {datetime.today().strftime('%Y-%m-%d')}
    </div>

    <h3>Booking KPI (T-1)</h3>
    {booking_kpi.to_html(index=False)}

    <h3>Container Status</h3>
    {status_summary.to_html(index=False)}

    <h3>Current Movement</h3>
    {current_movement.to_html(index=False)}

    <h3>Upcoming Movement</h3>
    {upcoming_movement.to_html(index=False)}

    <h3>Exceptions ({exception_email['Container'].nunique()})</h3>
    {exception_email.to_html(index=False)}

    <h3>LFD Risk ({lfd_email['Container'].nunique()})</h3>
    {lfd_email.to_html(index=False)}

    <h3>Documentation Risk ({doc_email['Container'].nunique()})</h3>
    {doc_email.to_html(index=False)}

    <h3>Missing Data ({issues_email['Container'].nunique()})</h3>
    {issues_email.to_html(index=False)}

    <h3>Additional Notes</h3>

    <div class='note'>
    {custom_note if custom_note else 'No additional notes.'}
    </div>

    <br>

    Regards,<br>
    <b>Logistics Control Tower</b>

    </body>
    </html>

    """

    # =========================================================================
    # PREVIEW
    # =========================================================================

    st.subheader("📄 Email Preview")

    st.components.v1.html(
        html,
        height=850,
        scrolling=True
    )

    # =========================================================================
    # SEND
    # =========================================================================

    if st.button("📨 Open Outlook Draft"):

        try:

            outlook = win32.Dispatch(
                "Outlook.Application"
            )

            mail = outlook.CreateItem(0)

            mail.To = recipients
            mail.CC = cc

            mail.Subject = (
                f"Daily Logistics MOM | "
                f"{datetime.today().strftime('%Y-%m-%d')}"
            )

            mail.HTMLBody = html

            mail.Display()

            st.success(
                "Outlook draft opened successfully"
            )

        except Exception as e:

            st.error(f"Error: {e}")