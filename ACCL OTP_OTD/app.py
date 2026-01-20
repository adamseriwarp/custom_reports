import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime

def check_password():
    """Returns `True` if the user has entered the correct password."""
    
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == st.secrets["app"]["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password incorrect, show input + error
        st.text_input(
            "Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct
        return True

# Database connection settings from Streamlit Secrets
def get_db_config():
    return {
        "host": st.secrets["mysql"]["host"],
        "port": st.secrets["mysql"]["port"],
        "user": st.secrets["mysql"]["user"],
        "password": st.secrets["mysql"]["password"],
        "database": st.secrets["mysql"]["database"]
    }

@st.cache_data(ttl=600)
def fetch_otp_data(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch OTP report data from MySQL database."""
    query = """
    SELECT 
        o.loadId,
        o.warpId,
        o.pickTimeArrived,
        o.dropTimeArrived,
        o.pickWindowFrom,
        o.dropWindowFrom,
        r.palletCount,
        r.transitCost
    FROM otp_reports o
    LEFT JOIN routes r ON o.loadId = r.routeId
    WHERE o.pickWindowFrom IS NOT NULL 
      AND o.pickWindowFrom != ''
      AND o.pickWindowFrom != 'Invalid date'
      AND o.carrierName = 'Accelerated USA Inc'
      AND o.shipmentStatus = 'Complete'
    """
    
    conn = mysql.connector.connect(**get_db_config())
    df = pd.read_sql(query, conn)
    conn.close()
    
    # Convert pickWindowFrom to datetime and filter by date range
    df['pickWindowFrom_dt'] = pd.to_datetime(df['pickWindowFrom'], format='%m/%d/%Y %H:%M:%S', errors='coerce')
    
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    
    df = df[(df['pickWindowFrom_dt'] >= start) & (df['pickWindowFrom_dt'] <= end)]
    df = df.drop(columns=['pickWindowFrom_dt'])
    
    return df

def calculate_transit_times(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate transit times for each loadId."""
    if df.empty:
        return pd.DataFrame()

    # Convert to datetime
    df['pickTimeArrived'] = pd.to_datetime(df['pickTimeArrived'], errors='coerce')
    df['dropTimeArrived'] = pd.to_datetime(df['dropTimeArrived'], errors='coerce')
    df['pickWindowFrom'] = pd.to_datetime(df['pickWindowFrom'], errors='coerce')
    df['dropWindowFrom'] = pd.to_datetime(df['dropWindowFrom'], errors='coerce')

    # Since each loadId has only one warpId (with shipmentStatus = Complete), just use the values directly
    result = df[['loadId', 'warpId', 'pickTimeArrived', 'dropTimeArrived', 
                 'pickWindowFrom', 'dropWindowFrom', 'palletCount', 'transitCost']].copy()

    # Calculate transit days
    result['Transit Days'] = (
        result['dropTimeArrived'] - result['pickTimeArrived']
    ).dt.total_seconds() / (24 * 3600)
    result['Transit Days'] = result['Transit Days'].round(2)

    # Calculate OTP (On Time Pickup)
    actual_pick_date = result['pickTimeArrived'].dt.normalize()
    scheduled_pick_date = result['pickWindowFrom'].dt.normalize()
    result['OTP'] = (actual_pick_date > scheduled_pick_date).map({True: 'Late', False: 'On Time'})
    result['OTP Days Late'] = (actual_pick_date - scheduled_pick_date).dt.days
    result.loc[result['OTP Days Late'] < 0, 'OTP Days Late'] = 0

    # Calculate OTD (On Time Delivery)
    actual_drop_date = result['dropTimeArrived'].dt.normalize()
    scheduled_drop_date = result['dropWindowFrom'].dt.normalize()
    result['OTD'] = (actual_drop_date > scheduled_drop_date).map({True: 'Late', False: 'On Time'})
    result['OTD Days Late'] = (actual_drop_date - scheduled_drop_date).dt.days
    result.loc[result['OTD Days Late'] < 0, 'OTD Days Late'] = 0

    # Rename columns for clarity
    result = result.rename(columns={
        'loadId': 'Load ID',
        'warpId': 'WarpID',
        'pickWindowFrom': 'Scheduled Pickup',
        'pickTimeArrived': 'Actual Pickup',
        'dropWindowFrom': 'Scheduled Dropoff',
        'dropTimeArrived': 'Actual Dropoff',
        'palletCount': 'Pallet Count',
        'transitCost': 'Transit Cost'
    })
    
    # Reorder columns
    result = result[['Load ID', 'WarpID', 'Scheduled Pickup', 'Actual Pickup', 'OTP', 'OTP Days Late',
                     'Scheduled Dropoff', 'Actual Dropoff', 'OTD', 'OTD Days Late', 
                     'Transit Days', 'Pallet Count', 'Transit Cost']]
    
    return result

def main():
    st.set_page_config(page_title="ACCL OTP/OTD Report", layout="wide")
    st.title("📊 ACCL OTP/OTD Transit Time Report")
    
    if not check_password():
        st.stop()
    
    st.markdown("Carrier: **Accelerated USA Inc** | Status: **Complete**")
    
    # Date range selector
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input("Start Date", datetime(2024, 12, 15))
    with col2:
        end_date = st.date_input("End Date", datetime(2025, 1, 15))
    
    if start_date > end_date:
        st.error("Start date must be before end date!")
        return
    
    # Fetch data button
    if st.sidebar.button("🔄 Fetch Data", type="primary"):
        with st.spinner("Fetching data from database..."):
            try:
                raw_data = fetch_otp_data(str(start_date), str(end_date))
                st.session_state['raw_data'] = raw_data
                st.session_state['transit_data'] = calculate_transit_times(raw_data)
            except Exception as e:
                st.error(f"Error fetching data: {e}")
                return
    
    # Display results
    if 'transit_data' in st.session_state and not st.session_state['transit_data'].empty:
        transit_df = st.session_state['transit_data']
        
        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Loads", len(transit_df))
        with col2:
            avg_transit = transit_df['Transit Days'].mean()
            st.metric("Avg Transit Days", f"{avg_transit:.2f}" if pd.notna(avg_transit) else "N/A")
        with col3:
            min_transit = transit_df['Transit Days'].min()
            st.metric("Min Transit Days", f"{min_transit:.2f}" if pd.notna(min_transit) else "N/A")
        with col4:
            max_transit = transit_df['Transit Days'].max()
            st.metric("Max Transit Days", f"{max_transit:.2f}" if pd.notna(max_transit) else "N/A")
        
        st.markdown("---")
        st.subheader("📊 Transit Time Data")
        st.dataframe(transit_df, use_container_width=True, hide_index=True)
        
        # Download button
        csv = transit_df.to_csv(index=False)
        st.download_button(
            label="📥 Download CSV",
            data=csv,
            file_name=f"accl_otp_otd_report_{start_date}_to_{end_date}.csv",
            mime="text/csv"
        )
    elif 'transit_data' in st.session_state:
        st.warning("No data found for the selected date range.")
    else:
        st.info("👈 Select a date range and click 'Fetch Data' to load the report.")

if __name__ == "__main__":
    main()
