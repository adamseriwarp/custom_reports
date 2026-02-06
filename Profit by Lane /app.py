import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from db_connection import execute_query
from auth import check_password

st.set_page_config(
    page_title="Profit by Lane Dashboard",
    page_icon="📊",
    layout="wide"
)

if not check_password():
    st.stop()

st.title("📊 Profit by Lane - Summary View")

# --- Sidebar Filters ---
st.sidebar.header("Filters")

# Shipment Type filter (important for logic)
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=["All", "Full Truckload", "Less Than Truckload"],
    index=0
)

# Date range filter
col1, col2 = st.sidebar.columns(2)
default_start = datetime.now() - timedelta(days=30)
default_end = datetime.now()

start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Get filter options from database
@st.cache_data(ttl=3600)
def get_filter_options():
    """Get unique values for filter dropdowns"""
    carriers_query = """
        SELECT DISTINCT carrierName
        FROM otp_reports
        WHERE carrierName IS NOT NULL AND carrierName != ''
        ORDER BY carrierName
        LIMIT 500
    """
    customers_query = """
        SELECT DISTINCT clientName
        FROM otp_reports
        WHERE clientName IS NOT NULL AND clientName != ''
        ORDER BY clientName
        LIMIT 500
    """
    lanes_query = """
        SELECT DISTINCT CONCAT(startMarket, ' → ', endMarket) as lane
        FROM otp_reports
        WHERE startMarket IS NOT NULL AND startMarket != ''
          AND endMarket IS NOT NULL AND endMarket != ''
        ORDER BY lane
        LIMIT 500
    """

    carriers_df = execute_query(carriers_query)
    customers_df = execute_query(customers_query)
    lanes_df = execute_query(lanes_query)

    carriers = carriers_df['carrierName'].tolist() if carriers_df is not None else []
    customers = customers_df['clientName'].tolist() if customers_df is not None else []
    lanes = lanes_df['lane'].tolist() if lanes_df is not None else []

    return carriers, customers, lanes

carriers, customers, lanes = get_filter_options()

# Filter selections
selected_carriers = st.sidebar.multiselect("Carrier", options=carriers, default=[])
selected_customers = st.sidebar.multiselect("Customer", options=customers, default=[])
selected_lanes = st.sidebar.multiselect("Lane", options=lanes, default=[])


# --- Main Query ---
@st.cache_data(ttl=300)
def get_profit_by_lane_data(start_date, end_date, carriers, customers, lanes, shipment_type):
    """
    Get profit by lane data with cross-dock cost breakdown.

    Logic by shipment type:
    - FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
    - LTL Direct (single row): Use that row
    - LTL Multi-leg: Use ONLY NO rows (YES row duplicates revenue)
    """

    # Build base WHERE conditions
    base_conditions = [
        "shipmentStatus = 'Complete'",
        "startMarket IS NOT NULL AND startMarket != ''",
        "endMarket IS NOT NULL AND endMarket != ''",
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '{start_date}'",
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') <= '{end_date}'"
    ]

    if carriers:
        carriers_str = "', '".join(carriers)
        base_conditions.append(f"carrierName IN ('{carriers_str}')")

    if customers:
        customers_str = "', '".join(customers)
        base_conditions.append(f"clientName IN ('{customers_str}')")

    if lanes:
        lanes_str = "', '".join(lanes)
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) IN ('{lanes_str}')")

    base_where = " AND ".join(base_conditions)

    if shipment_type == "Full Truckload":
        # FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
        query = f"""
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Full Truckload'
        GROUP BY startMarket, endMarket
        ORDER BY total_profit DESC
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Need to handle direct vs multi-leg differently
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                COUNT(*) as total_rows,
                SUM(CASE WHEN mainShipment = 'YES' THEN 1 ELSE 0 END) as yes_count,
                SUM(CASE WHEN mainShipment = 'NO' THEN 1 ELSE 0 END) as no_count
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType = 'Less Than Truckload'
            GROUP BY orderCode
        ),
        filtered_rows AS (
            SELECT o.*
            FROM otp_reports o
            JOIN order_row_counts orc ON o.orderCode = orc.orderCode
            WHERE {base_where}
              AND o.shipmentType = 'Less Than Truckload'
              AND (
                (orc.total_rows > 1 AND o.mainShipment = 'NO')
                OR orc.total_rows = 1
              )
        )
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost
        FROM filtered_rows
        GROUP BY startMarket, endMarket
        ORDER BY total_profit DESC
        """

    else:
        # All shipment types - combine FTL logic + LTL logic
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                shipmentType,
                COUNT(*) as total_rows,
                SUM(CASE WHEN mainShipment = 'YES' THEN 1 ELSE 0 END) as yes_count,
                SUM(CASE WHEN mainShipment = 'NO' THEN 1 ELSE 0 END) as no_count
            FROM otp_reports
            WHERE {base_where}
            GROUP BY orderCode, shipmentType
        ),
        filtered_rows AS (
            SELECT o.*
            FROM otp_reports o
            JOIN order_row_counts orc ON o.orderCode = orc.orderCode
            WHERE {base_where}
              AND (
                o.shipmentType = 'Full Truckload'
                OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows > 1 AND o.mainShipment = 'NO')
                OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows = 1)
                OR (o.shipmentType NOT IN ('Full Truckload', 'Less Than Truckload') AND o.mainShipment = 'YES')
              )
        )
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost
        FROM filtered_rows
        GROUP BY startMarket, endMarket
        ORDER BY total_profit DESC
        """

    return execute_query(query)


# Load data
with st.spinner("Loading data..."):
    df = get_profit_by_lane_data(
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
        selected_carriers,
        selected_customers,
        selected_lanes,
        shipment_type
    )

if df is not None and len(df) > 0:
    # Calculate cross-dock cost percentage
    df['crossdock_cost_pct'] = (df['crossdock_cost'] / df['total_cost'] * 100).fillna(0).round(1)
    df['margin_pct'] = (df['total_profit'] / df['total_revenue'] * 100).fillna(0).round(1)

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Revenue", f"${df['total_revenue'].sum():,.0f}")
    col2.metric("Total Cost", f"${df['total_cost'].sum():,.0f}")
    col3.metric("Total Profit", f"${df['total_profit'].sum():,.0f}")
    total_crossdock_pct = (df['crossdock_cost'].sum() / df['total_cost'].sum() * 100) if df['total_cost'].sum() > 0 else 0
    col4.metric("Cross-dock Cost %", f"{total_crossdock_pct:.1f}%")

    st.markdown("---")

    # Display pivot table
    st.subheader("Profit by Lane")

    display_df = df[['lane', 'order_count', 'total_revenue', 'total_cost', 'total_profit',
                     'crossdock_cost', 'crossdock_cost_pct', 'margin_pct']].copy()
    display_df.columns = ['Lane', 'Orders', 'Revenue', 'Cost', 'Profit',
                          'Cross-dock Cost', 'Cross-dock %', 'Margin %']

    # Format currency columns
    st.dataframe(
        display_df.style.format({
            'Revenue': '${:,.0f}',
            'Cost': '${:,.0f}',
            'Profit': '${:,.0f}',
            'Cross-dock Cost': '${:,.0f}',
            'Cross-dock %': '{:.1f}%',
            'Margin %': '{:.1f}%'
        }),
        use_container_width=True,
        height=600
    )

    # Store selected filters in session state for drill-down page
    st.session_state['filters'] = {
        'start_date': start_date,
        'end_date': end_date,
        'carriers': selected_carriers,
        'customers': selected_customers,
        'lanes': selected_lanes,
        'shipment_type': shipment_type
    }

    st.info("👉 Go to the **Drill Down** page in the sidebar to see individual order details.")

else:
    st.warning("No data found for the selected filters. Try adjusting your date range or filters.")