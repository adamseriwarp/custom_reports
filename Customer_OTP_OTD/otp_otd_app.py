import streamlit as st
import pymysql
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go

# Force redeployment - 2026-02-19
st.set_page_config(page_title="OTP/OTD Report", layout="wide")

# Database connection
@st.cache_resource
def get_connection():
    return pymysql.connect(
        host=st.secrets["db"]["host"],
        port=st.secrets["db"]["port"],
        user=st.secrets["db"]["user"],
        password=st.secrets["db"]["password"],
        database=st.secrets["db"]["database"]
    )

@st.cache_data(ttl=300)
def get_clients():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT clientName FROM otp_reports WHERE clientName IS NOT NULL ORDER BY clientName")
    return [row[0] for row in cursor.fetchall()]

@st.cache_data(ttl=300)
def get_all_clients_summary(start_date, end_date):
    """Get OTP/OTD summary for all clients within date range."""
    conn = get_connection()
    cursor = conn.cursor()

    query = """
    WITH main_shipments AS (
        SELECT
            clientName,
            orderCode,
            pickWindowFrom,
            pickWindowTo,
            pickTimeArrived,
            dropWindowFrom,
            dropWindowTo,
            dropTimeArrived
        FROM otp_reports
        WHERE shipmentStatus = 'Complete'
          AND STR_TO_DATE(pickWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') >= %s
          AND STR_TO_DATE(pickWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') < %s
          AND orderCode IS NOT NULL AND orderCode != ''
          AND (pickLocationName IS NULL OR dropLocationName IS NULL OR pickLocationName != dropLocationName)
          AND mainShipment = 'YES'
          AND clientName IS NOT NULL
    )
    SELECT
        clientName,
        COUNT(*) as total_shipments,
        SUM(CASE
            WHEN pickTimeArrived IS NOT NULL AND pickTimeArrived != ''
                 AND STR_TO_DATE(pickTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s') <= STR_TO_DATE(pickWindowTo, '%%m/%%d/%%Y %%H:%%i:%%s')
            THEN 1 ELSE 0
        END) as otp_on_time,
        SUM(CASE
            WHEN pickTimeArrived IS NOT NULL AND pickTimeArrived != ''
            THEN 1 ELSE 0
        END) as otp_total,
        SUM(CASE
            WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != ''
                 AND STR_TO_DATE(dropTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s') <= STR_TO_DATE(dropWindowTo, '%%m/%%d/%%Y %%H:%%i:%%s')
            THEN 1 ELSE 0
        END) as otd_on_time,
        SUM(CASE
            WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != ''
            THEN 1 ELSE 0
        END) as otd_total
    FROM main_shipments
    GROUP BY clientName
    HAVING COUNT(*) > 0
    """

    cursor.execute(query, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    columns = ['Client Name', 'Total Shipments', 'OTP On Time', 'OTP Total', 'OTD On Time', 'OTD Total']
    df = pd.DataFrame(cursor.fetchall(), columns=columns)

    # Convert to integers to handle Decimal types from MySQL
    df['OTP On Time'] = pd.to_numeric(df['OTP On Time'], errors='coerce').fillna(0).astype(int)
    df['OTP Total'] = pd.to_numeric(df['OTP Total'], errors='coerce').fillna(0).astype(int)
    df['OTD On Time'] = pd.to_numeric(df['OTD On Time'], errors='coerce').fillna(0).astype(int)
    df['OTD Total'] = pd.to_numeric(df['OTD Total'], errors='coerce').fillna(0).astype(int)
    df['Total Shipments'] = pd.to_numeric(df['Total Shipments'], errors='coerce').fillna(0).astype(int)

    # Calculate rates with safe division (avoid division by zero)
    df['OTP Rate %'] = df.apply(lambda row: round(row['OTP On Time'] / row['OTP Total'] * 100, 1) if row['OTP Total'] > 0 else 0, axis=1)
    df['OTD Rate %'] = df.apply(lambda row: round(row['OTD On Time'] / row['OTD Total'] * 100, 1) if row['OTD Total'] > 0 else 0, axis=1)

    return df

def run_otp_otd_query(client_name, start_date, end_date):
    conn = get_connection()
    cursor = conn.cursor()
    
    query = """
    WITH main_shipments AS (
        SELECT
            orderCode,
            warpId,
            shipmentType,
            pickLocationName,
            dropLocationName,
            pickWindowFrom,
            pickWindowTo,
            pickTimeArrived,
            pickTimeDeparted,
            pickupDelayCode,
            dropWindowFrom,
            dropWindowTo,
            dropTimeArrived,
            dropTimeDeparted,
            deliveryDelayCode
        FROM otp_reports
        WHERE shipmentStatus = 'Complete'
          AND clientName = %s
          AND STR_TO_DATE(pickWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') >= %s
          AND STR_TO_DATE(pickWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') < %s
          AND orderCode IS NOT NULL AND orderCode != ''
          AND (pickLocationName IS NULL OR dropLocationName IS NULL OR pickLocationName != dropLocationName)
          AND mainShipment = 'YES'
    ),
    valid_legs AS (
        SELECT *
        FROM otp_reports
        WHERE orderCode IN (SELECT orderCode FROM main_shipments)
          AND (pickLocationName IS NULL OR dropLocationName IS NULL OR pickLocationName != dropLocationName)
    ),
    earliest_pickup_ranked AS (
        SELECT orderCode, pickupDelayCode,
            ROW_NUMBER() OVER (
                PARTITION BY orderCode
                ORDER BY STR_TO_DATE(pickWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') ASC
            ) as rn
        FROM valid_legs
        WHERE pickupDelayCode IS NOT NULL AND pickupDelayCode != ''
    ),
    latest_dropoff_ranked AS (
        SELECT orderCode, deliveryDelayCode,
            ROW_NUMBER() OVER (
                PARTITION BY orderCode
                ORDER BY STR_TO_DATE(dropWindowFrom, '%%m/%%d/%%Y %%H:%%i:%%s') DESC
            ) as rn
        FROM valid_legs
        WHERE deliveryDelayCode IS NOT NULL AND deliveryDelayCode != ''
    ),
    earliest_pickup_delay AS (
        SELECT orderCode, pickupDelayCode FROM earliest_pickup_ranked WHERE rn = 1
    ),
    latest_dropoff_delay AS (
        SELECT orderCode, deliveryDelayCode FROM latest_dropoff_ranked WHERE rn = 1
    )
    SELECT
        m.orderCode,
        m.warpId,
        m.shipmentType,
        m.pickLocationName,
        m.dropLocationName,
        CONCAT(m.pickWindowFrom, ' - ', m.pickWindowTo) as pickupWindow,
        m.pickTimeArrived,
        m.pickTimeDeparted,
        CASE
            WHEN m.pickTimeArrived IS NOT NULL AND m.pickTimeArrived != ''
                 AND m.pickTimeDeparted IS NOT NULL AND m.pickTimeDeparted != ''
            THEN ROUND(TIMESTAMPDIFF(MINUTE,
                STR_TO_DATE(m.pickTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s'),
                STR_TO_DATE(m.pickTimeDeparted, '%%m/%%d/%%Y %%H:%%i:%%s')) / 60.0, 2)
            ELSE NULL
        END as pickupLoadTime,
        COALESCE(NULLIF(m.pickupDelayCode, ''), ep.pickupDelayCode) as pickupDelayCode,
        CONCAT(m.dropWindowFrom, ' - ', m.dropWindowTo) as deliveryWindow,
        m.dropTimeArrived,
        m.dropTimeDeparted,
        CASE
            WHEN m.dropTimeArrived IS NOT NULL AND m.dropTimeArrived != ''
                 AND m.dropTimeDeparted IS NOT NULL AND m.dropTimeDeparted != ''
            THEN ROUND(TIMESTAMPDIFF(MINUTE,
                STR_TO_DATE(m.dropTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s'),
                STR_TO_DATE(m.dropTimeDeparted, '%%m/%%d/%%Y %%H:%%i:%%s')) / 60.0, 2)
            ELSE NULL
        END as dropoffLoadTime,
        COALESCE(NULLIF(m.deliveryDelayCode, ''), ld.deliveryDelayCode) as deliveryDelayCode,
        CASE
            WHEN m.pickTimeArrived IS NULL OR m.pickTimeArrived = '' THEN 'No Pickup Data'
            WHEN STR_TO_DATE(m.pickTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s') <= STR_TO_DATE(m.pickWindowTo, '%%m/%%d/%%Y %%H:%%i:%%s')
            THEN 'On Time'
            ELSE 'Late'
        END as OTP_Status,
        CASE
            WHEN m.dropTimeArrived IS NULL OR m.dropTimeArrived = '' THEN 'No Delivery Data'
            WHEN STR_TO_DATE(m.dropTimeArrived, '%%m/%%d/%%Y %%H:%%i:%%s') <= STR_TO_DATE(m.dropWindowTo, '%%m/%%d/%%Y %%H:%%i:%%s')
            THEN 'On Time'
            ELSE 'Late'
        END as OTD_Status,
        m.pickWindowFrom
    FROM main_shipments m
    LEFT JOIN earliest_pickup_delay ep ON m.orderCode = ep.orderCode
    LEFT JOIN latest_dropoff_delay ld ON m.orderCode = ld.orderCode
    """

    cursor.execute(query, (client_name, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    columns = ['Order Code', 'Warp ID', 'Shipment Type', 'Pickup Location', 'Delivery Location',
               'Pickup Window', 'Pickup Arrival', 'Pickup Departure', 'Pickup Load Time (hrs)', 'Pickup Delay Code',
               'Delivery Window', 'Delivery Arrival', 'Delivery Departure', 'Delivery Load Time (hrs)', 'Delivery Delay Code',
               'OTP Status', 'OTD Status', 'pickWindowFrom']
    return pd.DataFrame(cursor.fetchall(), columns=columns)

# Sidebar - Page Navigation
st.sidebar.header("Navigation")
page = st.sidebar.radio("Select Page", ["Client Summary", "Client Detail"])

st.sidebar.divider()
st.sidebar.header("Filters")

# Date filters (shared across pages)
col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("Start Date", datetime(2025, 12, 1))
with col2:
    end_date = st.date_input("End Date", datetime(2026, 1, 1))

# ============ PAGE 1: CLIENT SUMMARY ============
if page == "Client Summary":
    st.title("Client OTP/OTD Summary")

    # Client selection options for summary page
    clients = get_clients()

    selection_mode = st.sidebar.radio("Client Selection", ["Worst by OTD Rate", "Best by OTD Rate", "Select Specific Clients"])

    if selection_mode in ["Worst by OTD Rate", "Best by OTD Rate"]:
        num_clients = st.sidebar.slider("Number of Clients", min_value=5, max_value=50, value=20)
    else:
        selected_clients = st.sidebar.multiselect("Select Clients", clients, default=clients[:10] if len(clients) >= 10 else clients)

    if st.sidebar.button("Generate Summary", type="primary"):
        with st.spinner("Fetching client summary..."):
            summary_df = get_all_clients_summary(start_date, end_date)

        if len(summary_df) == 0:
            st.warning("No data found for the selected date range.")
        else:
            # Filter based on selection mode
            if selection_mode == "Worst by OTD Rate":
                # Sort by OTD Rate ascending (worst first), then take top N
                display_df = summary_df.sort_values('OTD Rate %', ascending=True).head(num_clients)
                st.subheader(f"Bottom {num_clients} Clients by OTD Rate")
            elif selection_mode == "Best by OTD Rate":
                # Sort by OTD Rate descending (best first), then take top N
                display_df = summary_df.sort_values('OTD Rate %', ascending=False).head(num_clients)
                st.subheader(f"Top {num_clients} Clients by OTD Rate")
            else:
                # Filter to selected clients
                display_df = summary_df[summary_df['Client Name'].isin(selected_clients)]
                display_df = display_df.sort_values('OTD Rate %', ascending=True)
                st.subheader(f"Selected Clients Summary ({len(display_df)} clients)")

            # Overall metrics
            total_shipments = display_df['Total Shipments'].sum()
            total_otp_on_time = display_df['OTP On Time'].sum()
            total_otp_total = display_df['OTP Total'].sum()
            total_otd_on_time = display_df['OTD On Time'].sum()
            total_otd_total = display_df['OTD Total'].sum()

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Clients", len(display_df))
            col2.metric("Total Shipments", f"{total_shipments:,}")
            col3.metric("Avg OTP Rate", f"{(total_otp_on_time/total_otp_total*100):.1f}%" if total_otp_total > 0 else "N/A")
            col4.metric("Avg OTD Rate", f"{(total_otd_on_time/total_otd_total*100):.1f}%" if total_otd_total > 0 else "N/A")

            st.divider()

            # Prepare display dataframe
            pivot_display = display_df[['Client Name', 'Total Shipments', 'OTP On Time', 'OTP Rate %', 'OTD On Time', 'OTD Rate %']].copy()
            pivot_display = pivot_display.reset_index(drop=True)
            pivot_display.index = pivot_display.index + 1  # Start index at 1

            # Style the dataframe with conditional formatting
            def color_rate(val):
                if val >= 95:
                    return 'background-color: #90EE90'  # Light green
                elif val >= 85:
                    return 'background-color: #FFFFE0'  # Light yellow
                else:
                    return 'background-color: #FFB6C1'  # Light red

            styled_df = pivot_display.style.applymap(color_rate, subset=['OTP Rate %', 'OTD Rate %'])

            st.dataframe(styled_df, use_container_width=True, height=600)

            # Download button
            csv = pivot_display.to_csv(index=True)
            st.download_button("Download CSV", csv, f"client_summary_{start_date}_{end_date}.csv", "text/csv")

# ============ PAGE 2: CLIENT DETAIL ============
else:
    st.title("On-Time Pickup (OTP) & On-Time Delivery (OTD) Report")

    clients = get_clients()
    selected_client = st.sidebar.selectbox("Select Client", clients, index=clients.index("Back to the Roots, Inc") if "Back to the Roots, Inc" in clients else 0)

    if st.sidebar.button("Run Report", type="primary"):
        with st.spinner("Running query..."):
            df = run_otp_otd_query(selected_client, start_date, end_date)

        if len(df) == 0:
            st.warning("No data found for the selected filters.")
        else:
            # Summary metrics
            st.subheader(f"Summary for {selected_client}")
            otp_on_time = len(df[df['OTP Status'] == 'On Time'])
            otp_late = len(df[df['OTP Status'] == 'Late'])
            otd_on_time = len(df[df['OTD Status'] == 'On Time'])
            otd_late = len(df[df['OTD Status'] == 'Late'])

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Total Shipments", len(df))
            col2.metric("OTP On Time", otp_on_time)
            col3.metric("OTP Rate", f"{otp_on_time/(otp_on_time+otp_late)*100:.1f}%" if otp_on_time+otp_late > 0 else "N/A")
            col4.metric("OTD On Time", otd_on_time)
            col5.metric("OTD Rate", f"{otd_on_time/(otd_on_time+otd_late)*100:.1f}%" if otd_on_time+otd_late > 0 else "N/A")

            # Trend Chart
            st.subheader("OTP/OTD Trend")

            # Parse dates for trend
            df['pickDate'] = pd.to_datetime(df['pickWindowFrom'], format='%m/%d/%Y %H:%M:%S', errors='coerce')

            # Auto-determine granularity based on date range
            date_range_days = (end_date - start_date).days
            if date_range_days <= 14:
                df['period'] = df['pickDate'].dt.date
            elif date_range_days <= 90:
                df['period'] = df['pickDate'].dt.to_period('W').apply(lambda x: x.start_time.date())
            else:
                df['period'] = df['pickDate'].dt.to_period('M').apply(lambda x: x.start_time.date())

            # Calculate rates per period
            trend_data = df.groupby('period').apply(
                lambda x: pd.Series({
                    'OTP Rate': (x['OTP Status'] == 'On Time').sum() / ((x['OTP Status'] == 'On Time').sum() + (x['OTP Status'] == 'Late').sum()) * 100 if ((x['OTP Status'] == 'On Time').sum() + (x['OTP Status'] == 'Late').sum()) > 0 else None,
                    'OTD Rate': (x['OTD Status'] == 'On Time').sum() / ((x['OTD Status'] == 'On Time').sum() + (x['OTD Status'] == 'Late').sum()) * 100 if ((x['OTD Status'] == 'On Time').sum() + (x['OTD Status'] == 'Late').sum()) > 0 else None,
                    'Shipments': len(x)
                })
            ).reset_index()

            trend_data = trend_data.dropna(subset=['OTP Rate', 'OTD Rate'])

            if len(trend_data) > 0:
                # Create line chart
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=trend_data['period'], y=trend_data['OTP Rate'],
                                         mode='lines+markers', name='OTP Rate', line=dict(color='blue')))
                fig.add_trace(go.Scatter(x=trend_data['period'], y=trend_data['OTD Rate'],
                                         mode='lines+markers', name='OTD Rate', line=dict(color='green')))

                fig.update_layout(
                    xaxis_title='Period',
                    yaxis_title='Rate (%)',
                    yaxis=dict(range=[0, 100]),
                    hovermode='x unified',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
                )

                st.plotly_chart(fig, use_container_width=True)

                # Show trend data table
                with st.expander("View Trend Data"):
                    trend_display = trend_data.copy()
                    trend_display['OTP Rate'] = trend_display['OTP Rate'].round(1).astype(str) + '%'
                    trend_display['OTD Rate'] = trend_display['OTD Rate'].round(1).astype(str) + '%'
                    trend_display['Shipments'] = trend_display['Shipments'].astype(int)
                    st.dataframe(trend_display, use_container_width=True, hide_index=True)
            else:
                st.info("Not enough data to display trend.")

            # Delay Code Histograms
            st.subheader("Delay Code Breakdown")

            col_pickup, col_dropoff = st.columns(2)

            with col_pickup:
                st.markdown("**Pickup Delay Codes**")
                # Filter for late pickups with delay codes
                pickup_delays = df[df['OTP Status'] == 'Late']['Pickup Delay Code'].fillna('No Code').replace('', 'No Code')
                pickup_delay_counts = pickup_delays.value_counts().reset_index()
                pickup_delay_counts.columns = ['Delay Code', 'Count']

                if len(pickup_delay_counts) > 0:
                    fig_pickup = go.Figure(data=[
                        go.Bar(x=pickup_delay_counts['Delay Code'], y=pickup_delay_counts['Count'], marker_color='indianred')
                    ])
                    fig_pickup.update_layout(
                        xaxis_title='Delay Code',
                        yaxis_title='Count',
                        height=350
                    )
                    st.plotly_chart(fig_pickup, use_container_width=True)
                else:
                    st.info("No late pickups to display.")

            with col_dropoff:
                st.markdown("**Delivery Delay Codes**")
                # Filter for late deliveries with delay codes
                delivery_delays = df[df['OTD Status'] == 'Late']['Delivery Delay Code'].fillna('No Code').replace('', 'No Code')
                delivery_delay_counts = delivery_delays.value_counts().reset_index()
                delivery_delay_counts.columns = ['Delay Code', 'Count']

                if len(delivery_delay_counts) > 0:
                    fig_delivery = go.Figure(data=[
                        go.Bar(x=delivery_delay_counts['Delay Code'], y=delivery_delay_counts['Count'], marker_color='steelblue')
                    ])
                    fig_delivery.update_layout(
                        xaxis_title='Delay Code',
                        yaxis_title='Count',
                        height=350
                    )
                    st.plotly_chart(fig_delivery, use_container_width=True)
                else:
                    st.info("No late deliveries to display.")

            # Data table
            st.subheader("Shipment Details")
            # Remove temporary/internal columns before display
            display_df = df.drop(columns=['pickDate', 'period', 'pickWindowFrom'], errors='ignore')
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Download button
            csv = display_df.to_csv(index=False)
            st.download_button("Download CSV", csv, f"otp_otd_{selected_client}_{start_date}_{end_date}.csv", "text/csv")

