import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query
from auth import check_password

if not check_password():
    st.stop()

st.title("🏙️ Within-Market Analysis")
st.markdown("*Orders where start market = end market (e.g., LAX → LAX)*")

# --- Sidebar Filters ---
st.sidebar.header("Filters")

# Shipment Type filter
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=["All", "Full Truckload", "Less Than Truckload", "Parcel"],
    index=0
)

# Cross-dock filter
include_crossdock = st.sidebar.checkbox("Include Cross-dock Legs", value=True,
    help="Cross-dock legs are where pickup location = drop location")

# Date range filter
col1, col2 = st.sidebar.columns(2)
default_start = datetime.now() - timedelta(days=30)
default_end = datetime.now()
start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Customer filter
@st.cache_data(ttl=3600)
def get_customers():
    query = """
        SELECT DISTINCT clientName
        FROM otp_reports
        WHERE clientName IS NOT NULL AND clientName != ''
        ORDER BY clientName
        LIMIT 500
    """
    df = execute_query(query)
    return df['clientName'].tolist() if df is not None else []

customers = get_customers()
selected_customers = st.sidebar.multiselect("Customer", options=customers)

# --- Main Query ---
@st.cache_data(ttl=300)
def get_market_data(start_date, end_date, customers, shipment_type, include_crossdock):
    """Get profit by market (same start/end market)."""
    
    base_conditions = [
        "shipmentStatus = 'Complete'",
        "startMarket IS NOT NULL AND startMarket != ''",
        "startMarket = endMarket",  # Same market filter
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '{start_date}'",
        f"STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') <= '{end_date}'"
    ]
    
    if customers:
        customers_str = "', '".join(customers)
        base_conditions.append(f"clientName IN ('{customers_str}')")
    
    if shipment_type != "All":
        base_conditions.append(f"shipmentType = '{shipment_type}'")
    
    if not include_crossdock:
        base_conditions.append("pickLocationName != dropLocationName")
    
    base_where = " AND ".join(base_conditions)
    
    query = f"""
    SELECT
        startMarket as market,
        COUNT(DISTINCT orderCode) as order_count,
        SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
        SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
        SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
        SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
        SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as crossdock_revenue
    FROM otp_reports
    WHERE {base_where}
    GROUP BY startMarket
    ORDER BY total_profit ASC
    """
    
    return execute_query(query)

# Execute query
df = get_market_data(
    start_date.strftime('%Y-%m-%d'),
    end_date.strftime('%Y-%m-%d'),
    selected_customers,
    shipment_type,
    include_crossdock
)

if df is not None and len(df) > 0:
    # Summary metrics
    total_revenue = df['total_revenue'].sum()
    total_cost = df['total_cost'].sum()
    total_profit = df['total_profit'].sum()
    total_orders = df['order_count'].sum()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Revenue", f"${total_revenue:,.0f}")
    col2.metric("Total Cost", f"${total_cost:,.0f}")
    col3.metric("Total Profit", f"${total_profit:,.0f}")
    col4.metric("Total Orders", f"{total_orders:,.0f}")
    
    st.markdown("---")
    st.subheader("Profit by Market")
    st.caption("Sorted by least profitable at top")
    
    # Format display
    display_df = df.copy()
    display_df['margin_pct'] = (display_df['total_profit'] / display_df['total_revenue'] * 100).fillna(0)
    display_df.columns = ['Market', 'Orders', 'Revenue', 'Cost', 'Profit', 'Cross-dock Cost', 'Cross-dock Revenue', 'Margin %']
    
    st.dataframe(
        display_df.style.format({
            'Revenue': '${:,.0f}',
            'Cost': '${:,.0f}',
            'Profit': '${:,.0f}',
            'Cross-dock Cost': '${:,.0f}',
            'Cross-dock Revenue': '${:,.0f}',
            'Margin %': '{:.1f}%'
        }),
        use_container_width=True,
        hide_index=True
    )
else:
    st.warning("No data found for the selected filters.")

