import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from st_aggrid import AgGrid, GridOptionsBuilder
from db_connection import query_to_dataframe
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

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
# If multiple rows have the same latest date, filter out warehouse locations
df['pickDateArrived'] = pd.to_datetime(df['pickDateArrived'])

def is_warehouse(location):
    """Check if a location is a warehouse (starts with WTCH-)."""
    if pd.isna(location) or location == '':
        return False
    return location.startswith('WTCH-')

def deduplicate_order(group):
    """For each orderCode, get the row with latest date, preferring non-warehouse locations."""
    # Get the max date for this orderCode
    max_date = group['pickDateArrived'].max()
    # Filter to only rows with the max date
    latest_rows = group[group['pickDateArrived'] == max_date]

    if len(latest_rows) == 1:
        return latest_rows.iloc[0]

    # Multiple rows with same latest date - filter out warehouses
    non_warehouse = latest_rows[~latest_rows['dropLocationName'].apply(is_warehouse)]

    if len(non_warehouse) >= 1:
        return non_warehouse.iloc[0]
    else:
        # All are warehouses, just return the first one
        return latest_rows.iloc[0]

df_deduped = df.groupby('orderCode', group_keys=False).apply(deduplicate_order).reset_index(drop=True)

# Prepare Shipments Breakdown table - only keep the columns we need
shipments_df = df_deduped[['orderCode', 'dropLocationName', 'pickDateArrived']].copy()
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

# Helper function to generate PDF
def generate_pdf(shipments_df, pivot_df, start_date, end_date):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title = Paragraph(f"KITH Shipments Report ({start_date} to {end_date})", styles['Heading1'])
    elements.append(title)
    elements.append(Spacer(1, 20))

    # Shipments Breakdown
    elements.append(Paragraph(f"Shipments Breakdown (Total: {len(shipments_df):,})", styles['Heading2']))
    elements.append(Spacer(1, 10))

    shipments_data = [shipments_df.columns.tolist()] + shipments_df.values.tolist()
    shipments_table = Table(shipments_data)
    shipments_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(shipments_table)
    elements.append(Spacer(1, 30))

    # Pivot table
    elements.append(Paragraph(f"Shipment Count by Delivery Location (Total: {len(pivot_df):,})", styles['Heading2']))
    elements.append(Spacer(1, 10))

    pivot_data = [pivot_df.columns.tolist()] + pivot_df.values.tolist()
    pivot_table = Table(pivot_data)
    pivot_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(pivot_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer

# Create clean copies for export BEFORE AgGrid touches them
export_shipments_df = shipments_df[['Shipment ID', 'Delivery Location', 'Pickup Date']].copy()
export_pivot_df = pivot_df[['Delivery Location', 'Shipment Count']].copy()

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

st.divider()

# Export section
st.subheader("Export Data")
col1, col2, col3 = st.columns(3)

with col1:
    csv_shipments = export_shipments_df.to_csv(index=False)
    st.download_button(
        label="📥 Download Shipments (CSV)",
        data=csv_shipments,
        file_name=f"kith_shipments_{start_date}_{end_date}.csv",
        mime="text/csv"
    )

with col2:
    csv_pivot = export_pivot_df.to_csv(index=False)
    st.download_button(
        label="📥 Download Location Summary (CSV)",
        data=csv_pivot,
        file_name=f"kith_location_summary_{start_date}_{end_date}.csv",
        mime="text/csv"
    )

with col3:
    pdf_buffer = generate_pdf(export_shipments_df, export_pivot_df, str(start_date), str(end_date))
    st.download_button(
        label="📄 Download Full Report (PDF)",
        data=pdf_buffer,
        file_name=f"kith_report_{start_date}_{end_date}.pdf",
        mime="application/pdf"
    )
