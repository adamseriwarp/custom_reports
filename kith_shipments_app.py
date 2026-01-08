import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from st_aggrid import AgGrid, GridOptionsBuilder
from db_connection import query_to_dataframe

st.set_page_config(page_title="KITH Shipments Report", layout="wide")

# Password protection
def check_password():
    """Returns `True` if the user has the correct password."""
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", ""):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("Incorrect password")
        return False
    else:
        return True

if not check_password():
    st.stop()

st.title("KITH Shipments Report")

# Date range filter and refresh button
col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=30))
with col2:
    end_date = st.date_input("End Date", value=datetime.now())
with col3:
    st.write("")  # Spacer to align button with date inputs
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

@st.cache_data(ttl=300)
def get_shipments_data(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch shipments data for KITH LOGISTICS LLC."""
    sql = f"""
    SELECT
        orderCode,
        dropLocationName,
        STR_TO_DATE(pickDateArrived, '%m/%d/%Y') as pickDateArrived
    FROM otp_reports
    WHERE clientName = 'KITH LOGISTICS LLC'
      AND shipmentStatus = 'Complete'
      AND pickDateArrived IS NOT NULL
      AND pickDateArrived != ''
      AND STR_TO_DATE(pickDateArrived, '%m/%d/%Y') >= '{start_date}'
      AND STR_TO_DATE(pickDateArrived, '%m/%d/%Y') <= '{end_date}'
    ORDER BY orderCode, STR_TO_DATE(pickDateArrived, '%m/%d/%Y') DESC
    """
    return query_to_dataframe(sql)

# Load data
with st.spinner("Loading data..."):
    df = get_shipments_data(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))

if df.empty:
    st.warning("No data found for the selected date range.")
    st.stop()

# Deduplicate: keep only the row with the latest pickDateArrived per orderCode
df['pickDateArrived'] = pd.to_datetime(df['pickDateArrived'])
df_deduped = df.loc[df.groupby('orderCode')['pickDateArrived'].idxmax()]

# Prepare Shipments Breakdown table
shipments_df = df_deduped.copy()
shipments_df['pickDateArrived'] = shipments_df['pickDateArrived'].dt.strftime('%Y-%m-%d')
shipments_df = shipments_df.rename(columns={
    'orderCode': 'Shipment ID',
    'dropLocationName': 'Delivery Location',
    'pickDateArrived': 'Pickup Date'
})
shipments_df = shipments_df.sort_values('Pickup Date', ascending=False).reset_index(drop=True)

# Prepare Pivot table: shipment count per delivery location
pivot_df = df_deduped.groupby('dropLocationName').agg(
    Shipment_Count=('orderCode', 'nunique')
).reset_index()
pivot_df = pivot_df.rename(columns={
    'dropLocationName': 'Delivery Location',
    'Shipment_Count': 'Shipment Count'
})
pivot_df = pivot_df.sort_values('Shipment Count', ascending=False).reset_index(drop=True)

# Display Shipments Breakdown table
st.subheader("Shipments Breakdown")
st.write(f"Total unique shipments: **{len(shipments_df):,}**")

gb1 = GridOptionsBuilder.from_dataframe(shipments_df)
gb1.configure_pagination(paginationAutoPageSize=True)
gb1.configure_default_column(sortable=True, filter=True, resizable=True)
grid_options1 = gb1.build()
AgGrid(shipments_df, gridOptions=grid_options1, height=400, theme="streamlit")

st.divider()

# Display Pivot table
st.subheader("Shipment Count by Delivery Location")
st.write(f"Total delivery locations: **{len(pivot_df):,}**")

gb2 = GridOptionsBuilder.from_dataframe(pivot_df)
gb2.configure_pagination(paginationAutoPageSize=True)
gb2.configure_default_column(sortable=True, filter=True, resizable=True)
grid_options2 = gb2.build()
AgGrid(pivot_df, gridOptions=grid_options2, height=400, theme="streamlit")

