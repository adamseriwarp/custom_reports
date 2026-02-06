import streamlit as st
import pandas as pd
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query

# Check if user is authenticated (handled by Summary_View.py)
if not st.session_state.get("password_correct", False):
    st.warning("Please log in from the main page")
    st.stop()

st.title("🔍 Drill Down")
st.markdown("View individual order rows that contribute to profit/revenue/cost")

# --- Sidebar Filters ---
st.sidebar.header("Drill Down Filters")

# Get filter options
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

# Use filters from main page if available
default_filters = st.session_state.get('filters', {})

# Date filters
from datetime import datetime, timedelta

# Shipment Type filter (matching main page)
default_shipment_type = default_filters.get('shipment_type', 'All')
shipment_type_options = ["All", "Full Truckload", "Less Than Truckload"]
default_idx = shipment_type_options.index(default_shipment_type) if default_shipment_type in shipment_type_options else 0
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=shipment_type_options,
    index=default_idx
)

col1, col2 = st.sidebar.columns(2)
default_start = default_filters.get('start_date', datetime.now() - timedelta(days=30))
default_end = default_filters.get('end_date', datetime.now())

start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Drill-down selection - choose ONE carrier OR ONE customer
drill_type = st.sidebar.radio("Drill down by:", ["Customer", "Carrier", "Lane"])

if drill_type == "Customer":
    selected_value = st.sidebar.selectbox("Select Customer", options=customers)
elif drill_type == "Carrier":
    selected_value = st.sidebar.selectbox("Select Carrier", options=carriers)
else:
    selected_value = st.sidebar.selectbox("Select Lane", options=lanes)

# Optional additional filters
st.sidebar.markdown("---")
st.sidebar.subheader("Additional Filters")
if drill_type != "Lane":
    selected_lane = st.sidebar.selectbox("Filter by Lane (optional)", options=["All"] + lanes)
else:
    selected_lane = "All"

# --- Query for detailed rows ---
@st.cache_data(ttl=300)
def get_order_details(start_date, end_date, drill_type, selected_value, selected_lane, shipment_type):
    """
    Get individual order rows for drill-down analysis.

    Logic by shipment type:
    - FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
    - LTL Direct (single row): Use that row
    - LTL Multi-leg: Use ONLY NO rows (YES row duplicates revenue)
    """

    # Build base WHERE conditions
    base_conditions = [
        "shipmentStatus = 'Complete'",
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '{start_date}'",
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') <= '{end_date}'"
    ]

    if drill_type == "Customer":
        base_conditions.append(f"clientName = '{selected_value}'")
    elif drill_type == "Carrier":
        base_conditions.append(f"carrierName = '{selected_value}'")
    else:  # Lane
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) = '{selected_value}'")

    if selected_lane != "All":
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) = '{selected_lane}'")

    base_where = " AND ".join(base_conditions)

    # Column selection for output (use 'o.' prefix for JOINs)
    select_cols_simple = """
        orderCode as `Order ID`,
        warpId as `Warp ID`,
        mainShipment as `Main Shipment`,
        CONCAT(startMarket, ' → ', endMarket) as `Lane`,
        clientName as `Customer`,
        carrierName as `Carrier`,
        pickLocationName as `Pickup Location`,
        dropLocationName as `Drop Location`,
        COALESCE(revenueAllocationNumber, 0) as `Revenue`,
        COALESCE(costAllocationNumber, 0) as `Cost`,
        COALESCE(revenueAllocationNumber, 0) - COALESCE(costAllocationNumber, 0) as `Profit`,
        CASE WHEN pickLocationName = dropLocationName THEN 'Yes' ELSE 'No' END as `Cross-dock`,
        shipmentType as `Shipment Type`,
        pickWindowFrom as `Pickup Window`
    """

    select_cols_aliased = """
        o.orderCode as `Order ID`,
        o.warpId as `Warp ID`,
        o.mainShipment as `Main Shipment`,
        CONCAT(o.startMarket, ' → ', o.endMarket) as `Lane`,
        o.clientName as `Customer`,
        o.carrierName as `Carrier`,
        o.pickLocationName as `Pickup Location`,
        o.dropLocationName as `Drop Location`,
        COALESCE(o.revenueAllocationNumber, 0) as `Revenue`,
        COALESCE(o.costAllocationNumber, 0) as `Cost`,
        COALESCE(o.revenueAllocationNumber, 0) - COALESCE(o.costAllocationNumber, 0) as `Profit`,
        CASE WHEN o.pickLocationName = o.dropLocationName THEN 'Yes' ELSE 'No' END as `Cross-dock`,
        o.shipmentType as `Shipment Type`,
        o.pickWindowFrom as `Pickup Window`
    """

    if shipment_type == "Full Truckload":
        # FTL: Use ALL rows (YES + NO) - no JOIN needed
        query = f"""
        SELECT {select_cols_simple}
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Full Truckload'
        ORDER BY orderCode, mainShipment DESC, warpId
        LIMIT 5000
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Direct = single row, Multi-leg = NO rows only - uses JOIN
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                COUNT(*) as total_rows
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType = 'Less Than Truckload'
            GROUP BY orderCode
        )
        SELECT {select_cols_aliased}
        FROM otp_reports o
        JOIN order_row_counts orc ON o.orderCode = orc.orderCode
        WHERE {base_where}
          AND o.shipmentType = 'Less Than Truckload'
          AND (
            (orc.total_rows > 1 AND o.mainShipment = 'NO')
            OR orc.total_rows = 1
          )
        ORDER BY o.orderCode, o.mainShipment DESC, o.warpId
        LIMIT 5000
        """

    else:
        # All: Combine FTL + LTL + other logic - uses JOIN
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                shipmentType,
                COUNT(*) as total_rows
            FROM otp_reports
            WHERE {base_where}
            GROUP BY orderCode, shipmentType
        )
        SELECT {select_cols_aliased}
        FROM otp_reports o
        JOIN order_row_counts orc ON o.orderCode = orc.orderCode
        WHERE {base_where}
          AND (
            o.shipmentType = 'Full Truckload'
            OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows > 1 AND o.mainShipment = 'NO')
            OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows = 1)
            OR (o.shipmentType NOT IN ('Full Truckload', 'Less Than Truckload') AND o.mainShipment = 'YES')
          )
        ORDER BY o.orderCode, o.mainShipment DESC, o.warpId
        LIMIT 5000
        """

    return execute_query(query)

if selected_value:
    with st.spinner("Loading order details..."):
        df = get_order_details(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            drill_type,
            selected_value,
            selected_lane,
            shipment_type
        )
    
    if df is not None and len(df) > 0:
        # Summary metrics
        st.subheader(f"Summary for {drill_type}: {selected_value}")
        
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Orders", f"{df['Order ID'].nunique():,}")
        col2.metric("Total Rows", f"{len(df):,}")
        col3.metric("Total Revenue", f"${df['Revenue'].sum():,.0f}")
        col4.metric("Total Cost", f"${df['Cost'].sum():,.0f}")
        col5.metric("Total Profit", f"${df['Profit'].sum():,.0f}")
        
        # Cross-dock breakdown
        crossdock_df = df[df['Cross-dock'] == 'Yes']
        crossdock_cost = crossdock_df['Cost'].sum()
        total_cost = df['Cost'].sum()
        crossdock_pct = (crossdock_cost / total_cost * 100) if total_cost > 0 else 0
        
        st.info(f"💡 Cross-dock handling costs: ${crossdock_cost:,.0f} ({crossdock_pct:.1f}% of total cost)")
        
        st.markdown("---")
        
        # Detailed table
        st.subheader("Order Details")
        
        st.dataframe(
            df.style.format({
                'Revenue': '${:,.2f}',
                'Cost': '${:,.2f}',
                'Profit': '${:,.2f}'
            }),
            use_container_width=True,
            height=600
        )
        
        # Download button
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Download as CSV",
            data=csv,
            file_name=f"drill_down_{drill_type}_{selected_value}_{start_date}_{end_date}.csv",
            mime="text/csv"
        )
    else:
        st.warning("No data found for the selected filters.")
else:
    st.info("Please select a customer, carrier, or lane from the sidebar to view order details.")

