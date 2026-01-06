import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
from db_connection import query_to_dataframe

st.set_page_config(page_title="Profit Analysis", layout="wide")
st.title("Profit Analysis")

# Date range filter
st.sidebar.header("Filters")
col1, col2 = st.sidebar.columns(2)

# Default to last 30 days
default_end = datetime.now()
default_start = default_end - timedelta(days=30)

start_date = col1.date_input("Start Date", value=default_start)
end_date = col2.date_input("End Date", value=default_end)

@st.cache_data(ttl=300)
def get_raw_data(start_date_str: str, end_date_str: str) -> pd.DataFrame:
    """
    Fetch raw data from the database.
    """
    sql = f"""
    SELECT
        o.pickZipcode,
        o.dropZipcode,
        o.customerRoute,
        o.loadId,
        o.warpId,
        o.revenueAllocationNumber,
        o.costAllocationNumber,
        o.shipmentType,
        o.mainShipment,
        o.pickWindowFrom
    FROM otp_reports o
    WHERE STR_TO_DATE(o.pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '{start_date_str}'
      AND STR_TO_DATE(o.pickWindowFrom, '%m/%d/%Y %H:%i:%s') <= '{end_date_str}'
      AND (o.loadStatus IS NULL OR o.loadStatus != 'Canceled')
      AND LOWER(o.pickZipcode) NOT LIKE '%test%'
      AND LOWER(o.dropZipcode) NOT LIKE '%test%'
    """

    df = query_to_dataframe(sql)

    if df.empty:
        return pd.DataFrame()

    # Convert numeric columns
    df['revenueAllocationNumber'] = pd.to_numeric(df['revenueAllocationNumber'], errors='coerce').fillna(0)
    df['costAllocationNumber'] = pd.to_numeric(df['costAllocationNumber'], errors='coerce').fillna(0)

    # Identify loadIds with multiple warpIds
    warp_counts = df.groupby('loadId')['warpId'].nunique().reset_index()
    warp_counts.columns = ['loadId', 'warp_count']
    df = df.merge(warp_counts, on='loadId', how='left')

    # Create shipment count flag
    df['count_for_shipment'] = ~(
        (df['warp_count'] > 1) &
        (df['shipmentType'] == 'Less Than Truckload') &
        (df['mainShipment'] == 'YES')
    )

    return df

def aggregate_by_column(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    Aggregate data by a specified column.
    """
    if df.empty:
        return pd.DataFrame()

    agg_df = df.groupby([group_col]).agg(
        Total_Revenue=('revenueAllocationNumber', 'sum'),
        Total_Cost=('costAllocationNumber', 'sum'),
        Shipment_Count=('warpId', lambda x: x[df.loc[x.index, 'count_for_shipment']].nunique())
    ).reset_index()

    # Calculate margin
    agg_df['Margin'] = agg_df['Total_Revenue'] - agg_df['Total_Cost']

    # Format columns
    agg_df['Total_Revenue'] = agg_df['Total_Revenue'].round(2)
    agg_df['Total_Cost'] = agg_df['Total_Cost'].round(2)
    agg_df['Margin'] = agg_df['Margin'].round(2)

    # Reorder columns
    agg_df = agg_df[[group_col, 'Total_Revenue', 'Total_Cost', 'Margin', 'Shipment_Count']]

    return agg_df

def build_grid(df: pd.DataFrame, first_col: str, first_col_header: str):
    """Build and display an AgGrid for the given dataframe."""
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_column(first_col, headerName=first_col_header)
    gb.configure_column("Total_Revenue", headerName="Revenue", type=["numericColumn"],
                       valueFormatter="'$' + value.toLocaleString()")
    gb.configure_column("Total_Cost", headerName="Cost", type=["numericColumn"],
                       valueFormatter="'$' + value.toLocaleString()")
    gb.configure_column("Margin", type=["numericColumn"],
                       valueFormatter="'$' + value.toLocaleString()")
    gb.configure_column("Shipment_Count", headerName="Shipment Count", type=["numericColumn"])
    gb.configure_default_column(sortable=True, filter=True, resizable=True)
    grid_options = gb.build()

    AgGrid(
        df,
        gridOptions=grid_options,
        height=400,
        theme='streamlit',
        enable_enterprise_modules=True,
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.MODEL_CHANGED
    )

def run_data_quality_checks(df: pd.DataFrame) -> dict:
    """
    Run diagnostic checks on the raw data to identify data quality issues.
    Returns a dictionary of findings with samples.
    """
    issues = {}

    # Check for null/empty customer routes
    null_routes_mask = df['customerRoute'].isna() | (df['customerRoute'] == '')
    null_routes_count = null_routes_mask.sum()
    if null_routes_count > 0:
        sample_df = df[null_routes_mask].copy()
        sample_loadids = sample_df[sample_df['loadId'].notna()]['loadId'].head(5).tolist()
        if len(sample_loadids) < 5:
            remaining = 5 - len(sample_loadids)
            sample_warpids = sample_df[sample_df['loadId'].isna()]['warpId'].head(remaining).tolist()
            sample_loadids.extend([f"warpId:{wid}" for wid in sample_warpids])
        issues['Missing Routes'] = {
            'description': f"{null_routes_count} records with null/empty customer routes",
            'samples': sample_loadids
        }

    # Check for null/invalid zip codes
    null_pick_zips = df['pickZipcode'].isna().sum()
    null_drop_zips = df['dropZipcode'].isna().sum()
    if null_pick_zips > 0 or null_drop_zips > 0:
        sample_df = df[df['pickZipcode'].isna() | df['dropZipcode'].isna()].copy()
        sample_loadids = sample_df[sample_df['loadId'].notna()]['loadId'].head(5).tolist()
        if len(sample_loadids) < 5:
            remaining = 5 - len(sample_loadids)
            sample_warpids = sample_df[sample_df['loadId'].isna()]['warpId'].head(remaining).tolist()
            sample_loadids.extend([f"warpId:{wid}" for wid in sample_warpids])
        issues['Missing Zip Codes'] = {
            'description': f"{null_pick_zips} pickup, {null_drop_zips} drop zip codes missing",
            'samples': sample_loadids
        }



    # Check for zero/negative revenue or cost at LOAD level (aggregate by loadId)
    load_aggregates = df.groupby('loadId').agg({
        'revenueAllocationNumber': 'sum',
        'costAllocationNumber': 'sum'
    }).reset_index()

    zero_revenue_mask = load_aggregates['revenueAllocationNumber'] == 0
    negative_revenue_mask = load_aggregates['revenueAllocationNumber'] < 0
    zero_revenue = zero_revenue_mask.sum()
    negative_revenue = negative_revenue_mask.sum()

    if zero_revenue > 0 or negative_revenue > 0:
        sample_loadids = load_aggregates[zero_revenue_mask | negative_revenue_mask]['loadId'].head(5).tolist()
        issues['Revenue Issues'] = {
            'description': f"{zero_revenue} loads with zero revenue, {negative_revenue} loads with negative revenue",
            'samples': sample_loadids
        }

    zero_cost_mask = load_aggregates['costAllocationNumber'] == 0
    negative_cost_mask = load_aggregates['costAllocationNumber'] < 0
    zero_cost = zero_cost_mask.sum()
    negative_cost = negative_cost_mask.sum()

    if zero_cost > 0 or negative_cost > 0:
        sample_loadids = load_aggregates[zero_cost_mask | negative_cost_mask]['loadId'].head(5).tolist()
        issues['Cost Issues'] = {
            'description': f"{zero_cost} loads with zero cost, {negative_cost} loads with negative cost",
            'samples': sample_loadids
        }

    # Check for duplicate warpIds
    duplicate_warps_mask = df['warpId'].duplicated()
    duplicate_warps = duplicate_warps_mask.sum()
    if duplicate_warps > 0:
        sample_warpids = df[duplicate_warps_mask]['warpId'].head(5).tolist()
        issues['Duplicate Records'] = {
            'description': f"{duplicate_warps} duplicate warpId entries",
            'samples': sample_warpids
        }

    # Check for routes with inconsistent formatting (e.g., extra spaces, special characters)
    routes_multiple_spaces_mask = df['customerRoute'].str.contains(r'\s{2,}', na=False, regex=True)
    routes_with_multiple_spaces = routes_multiple_spaces_mask.sum()
    if routes_with_multiple_spaces > 0:
        sample_routes = df[routes_multiple_spaces_mask]['customerRoute'].head(5).tolist()
        issues['Route Formatting'] = {
            'description': f"{routes_with_multiple_spaces} routes with multiple consecutive spaces",
            'samples': sample_routes
        }

    # Check for routes that don't follow expected pattern (city, state>city, state)
    routes_no_arrow_mask = ~df['customerRoute'].str.contains('>', na=False)
    routes_without_arrow = routes_no_arrow_mask.sum()
    if routes_without_arrow > 0:
        sample_routes = df[routes_no_arrow_mask]['customerRoute'].head(5).tolist()
        issues['Route Pattern'] = {
            'description': f"{routes_without_arrow} routes missing '>' separator",
            'samples': sample_routes
        }

    # Check for missing shipment type or main shipment flag
    missing_type_mask = df['shipmentType'].isna()
    missing_main_mask = df['mainShipment'].isna()
    missing_shipment_type = missing_type_mask.sum()
    missing_main_shipment = missing_main_mask.sum()
    if missing_shipment_type > 0 or missing_main_shipment > 0:
        sample_df = df[missing_type_mask | missing_main_mask].copy()
        sample_loadids = sample_df[sample_df['loadId'].notna()]['loadId'].head(5).tolist()
        if len(sample_loadids) < 5:
            remaining = 5 - len(sample_loadids)
            sample_warpids = sample_df[sample_df['loadId'].isna()]['warpId'].head(remaining).tolist()
            sample_loadids.extend([f"warpId:{wid}" for wid in sample_warpids])
        issues['Missing Metadata'] = {
            'description': f"{missing_shipment_type} missing shipment type, {missing_main_shipment} missing main shipment flag",
            'samples': sample_loadids
        }

    return issues

# Fetch data
if st.sidebar.button("Load Data", type="primary") or 'data_loaded' not in st.session_state:
    with st.spinner("Fetching data from database..."):
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        raw_df = get_raw_data(start_str, end_str)
        st.session_state['raw_data'] = raw_df
        st.session_state['data_loaded'] = True

if 'raw_data' in st.session_state and not st.session_state['raw_data'].empty:
    raw_df = st.session_state['raw_data']

    # Data Quality Diagnostics
    with st.expander("📊 Data Quality Diagnostics", expanded=False):
        quality_issues = run_data_quality_checks(raw_df)

        if quality_issues:
            st.warning(f"Found {len(quality_issues)} data quality issue(s)")
            for issue_type, issue_data in quality_issues.items():
                st.write(f"**{issue_type}:** {issue_data['description']}")
                if issue_data['samples']:
                    # Filter out any None values and join with single comma
                    clean_samples = [str(s) for s in issue_data['samples'] if s is not None and str(s) != 'nan']
                    if clean_samples:
                        st.write(f"   _Sample (up to 5):_ {', '.join(clean_samples)}")
                st.write("")  # Add spacing
        else:
            st.success("No data quality issues detected!")

        # Additional stats
        st.write("---")
        st.write("**Dataset Overview:**")
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Records", f"{len(raw_df):,}")
        col2.metric("Unique Routes", f"{raw_df['customerRoute'].nunique():,}")
        col3.metric("Date Range", f"{(pd.to_datetime(raw_df['pickWindowFrom'], format='%m/%d/%Y %H:%M:%S').max() - pd.to_datetime(raw_df['pickWindowFrom'], format='%m/%d/%Y %H:%M:%S').min()).days} days")

    # Create aggregated dataframes
    df_pick = aggregate_by_column(raw_df, 'pickZipcode')
    df_drop = aggregate_by_column(raw_df, 'dropZipcode')
    df_route = aggregate_by_column(raw_df, 'customerRoute')

    # Summary metrics (using pickZipcode aggregation)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Revenue", f"${df_pick['Total_Revenue'].sum():,.2f}")
    col2.metric("Total Cost", f"${df_pick['Total_Cost'].sum():,.2f}")
    col3.metric("Total Margin", f"${df_pick['Margin'].sum():,.2f}")
    col4.metric("Total Shipments", f"{df_pick['Shipment_Count'].sum():,}")

    st.divider()

    # Table 1: By Pickup Zip Code
    st.subheader("By Pickup Zip Code")
    build_grid(df_pick, 'pickZipcode', 'Pickup Zip Code')

    st.divider()

    # Table 2: By Drop Zip Code
    st.subheader("By Drop Zip Code")
    build_grid(df_drop, 'dropZipcode', 'Drop Zip Code')

    st.divider()

    # Table 3: By Customer Route
    st.subheader("By Customer Route")
    build_grid(df_route, 'customerRoute', 'Customer Route')
else:
    st.info("Click 'Load Data' to fetch data, or adjust the date range and try again.")

