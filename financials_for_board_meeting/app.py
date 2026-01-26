import streamlit as st
import pandas as pd
import numpy as np
from scipy import stats
import plotly.express as px
import plotly.graph_objects as go
from db_connection import test_connection, execute_query

# Page configuration
st.set_page_config(
    page_title="LTL Financial Reports - Board Meeting",
    page_icon="📊",
    layout="wide"
)

# Title
st.title("📊 LTL Market Financial Report - 2025")

# Helper function to get quarter from date string (MM/DD/YYYY HH:MM:SS)
def get_quarter_case():
    return """
        CASE
            WHEN pickWindowFrom LIKE '01/%/2025%' OR pickWindowFrom LIKE '02/%/2025%' OR pickWindowFrom LIKE '03/%/2025%' THEN 'Q1'
            WHEN pickWindowFrom LIKE '04/%/2025%' OR pickWindowFrom LIKE '05/%/2025%' OR pickWindowFrom LIKE '06/%/2025%' THEN 'Q2'
            WHEN pickWindowFrom LIKE '07/%/2025%' OR pickWindowFrom LIKE '08/%/2025%' OR pickWindowFrom LIKE '09/%/2025%' THEN 'Q3'
            WHEN pickWindowFrom LIKE '10/%/2025%' OR pickWindowFrom LIKE '11/%/2025%' OR pickWindowFrom LIKE '12/%/2025%' THEN 'Q4'
            ELSE 'Unknown'
        END
    """

def get_market_case(location_col):
    """
    SQL CASE statement to extract market code from various crossdock patterns.

    Patterns supported:
    - WTCH-{AIRPORT}-{#} → 3-letter airport code (e.g., WTCH-LAX-9 → LAX)
    - ACCL-{AIRPORT} → 3-letter airport code (e.g., ACCL-EWR → EWR)
    - SB-{CITY}-{CODE} → mapped to airport code (e.g., SB-NYC-UG → EWR)
    - Cross-Dock locations → mapped to nearest airport
    """
    return f"""
        CASE
            WHEN {location_col} LIKE 'WTCH-%' THEN SUBSTRING({location_col}, 6, 3)
            WHEN {location_col} LIKE 'ACCL-%' THEN SUBSTRING({location_col}, 6, 3)
            WHEN {location_col} LIKE 'SB-ATL-%' THEN 'ATL'
            WHEN {location_col} LIKE 'SB-DC-%' THEN 'DCA'
            WHEN {location_col} LIKE 'SB-DAL-%' THEN 'DFW'
            WHEN {location_col} LIKE 'SB-SEA-%' THEN 'SEA'
            WHEN {location_col} LIKE 'SB-LA-%' THEN 'LAX'
            WHEN {location_col} LIKE 'SB-DEN-%' THEN 'DEN'
            WHEN {location_col} LIKE 'SB-NYC-%' THEN 'EWR'
            WHEN {location_col} LIKE 'SB-PHX-%' THEN 'PHX'
            WHEN {location_col} LIKE 'SB-MIA-%' THEN 'MIA'
            WHEN {location_col} LIKE '%GoBolt%NYC%Cross-Dock%' THEN 'EWR'
            WHEN {location_col} LIKE '%Cross-Dock%Chicago%' THEN 'ORD'
            ELSE NULL
        END
    """

def is_crossdock(location_col):
    """SQL condition to check if a location is any type of crossdock"""
    return f"""
        ({location_col} LIKE 'WTCH-%'
         OR {location_col} LIKE 'ACCL-%'
         OR {location_col} LIKE 'SB-%'
         OR {location_col} LIKE '%Cross-Dock%')
    """

@st.cache_data(ttl=300)
def get_market_summary():
    """Get market-level summary for LTL data from all crossdock patterns"""

    # Market extraction for pick and drop locations
    pick_market = get_market_case('pickLocationName')
    drop_market = get_market_case('dropLocationName')

    # Crossdock checks
    pick_is_xdock = is_crossdock('pickLocationName')
    drop_is_xdock = is_crossdock('dropLocationName')

    # LTL Query - includes all crossdock patterns
    ltl_query = f"""
        SELECT
            CASE
                WHEN {pick_market} IS NOT NULL THEN {pick_market}
                WHEN {drop_market} IS NOT NULL THEN {drop_market}
            END as market,
            {get_quarter_case()} as quarter,
            COUNT(DISTINCT warpId) as shipments,
            SUM(CASE WHEN revenueAllocationNumber > 0 THEN revenueAllocationNumber ELSE 0 END) as revenue,
            SUM(COALESCE(pieces, 0)) as pieces
        FROM otp_reports
        WHERE shipmentType = 'Less Than Truckload'
          AND mainShipment = 'YES'
          AND shipmentStatus = 'Complete'
          AND pickWindowFrom LIKE '%/2025%'
          AND (
              ({pick_is_xdock} AND (NOT {drop_is_xdock} OR dropLocationName IS NULL))
              OR
              ({drop_is_xdock} AND (NOT {pick_is_xdock} OR pickLocationName IS NULL))
          )
        GROUP BY market, quarter
        HAVING market IS NOT NULL
    """

    # Market extraction for subquery (using m. prefix)
    m_pick_market = get_market_case('m.pickLocationName')
    m_drop_market = get_market_case('m.dropLocationName')
    m_pick_is_xdock = is_crossdock('m.pickLocationName')
    m_drop_is_xdock = is_crossdock('m.dropLocationName')

    # LTL Cost Query - sum all costs in orderCode
    ltl_cost_query = f"""
        SELECT
            sub.market,
            sub.quarter,
            SUM(sub.order_cost) as cost
        FROM (
            SELECT
                o.orderCode,
                CASE
                    WHEN {m_pick_market} IS NOT NULL THEN {m_pick_market}
                    WHEN {m_drop_market} IS NOT NULL THEN {m_drop_market}
                END as market,
                {get_quarter_case().replace('pickWindowFrom', 'm.pickWindowFrom')} as quarter,
                SUM(o.costAllocationNumber) as order_cost
            FROM otp_reports o
            INNER JOIN (
                SELECT orderCode, pickLocationName, dropLocationName, pickWindowFrom
                FROM otp_reports
                WHERE shipmentType = 'Less Than Truckload'
                  AND mainShipment = 'YES'
                  AND shipmentStatus = 'Complete'
                  AND pickWindowFrom LIKE '%/2025%'
                  AND (
                      ({pick_is_xdock} AND (NOT {drop_is_xdock} OR dropLocationName IS NULL))
                      OR
                      ({drop_is_xdock} AND (NOT {pick_is_xdock} OR pickLocationName IS NULL))
                  )
            ) m ON o.orderCode = m.orderCode
            WHERE o.shipmentType = 'Less Than Truckload'
              AND o.shipmentStatus = 'Complete'
            GROUP BY o.orderCode, market, quarter
        ) sub
        WHERE sub.market IS NOT NULL
        GROUP BY sub.market, sub.quarter
    """

    ltl_df = execute_query(ltl_query)
    ltl_cost_df = execute_query(ltl_cost_query)

    # Merge LTL revenue/pieces with cost
    if ltl_df is not None and ltl_cost_df is not None:
        ltl_df = ltl_df.merge(ltl_cost_df, on=['market', 'quarter'], how='left')
        ltl_df['cost'] = ltl_df['cost'].fillna(0)
    elif ltl_df is None:
        return None

    # Convert decimal columns to float (MySQL returns Decimal type)
    numeric_cols = ['shipments', 'revenue', 'cost', 'pieces']
    for col in numeric_cols:
        if col in ltl_df.columns:
            ltl_df[col] = ltl_df[col].astype(float)

    return ltl_df

# Load data
with st.spinner("Loading market data..."):
    data = get_market_summary()

if data is None or data.empty:
    st.error("Unable to load market data. Please check database connection.")
    st.stop()

# Aggregate by market for summary view
market_summary = data.groupby('market').agg({
    'shipments': 'sum',
    'revenue': 'sum',
    'cost': 'sum',
    'pieces': 'sum'
}).reset_index()

# Convert to float after aggregation (groupby can produce Decimal types)
for col in ['shipments', 'revenue', 'cost', 'pieces']:
    market_summary[col] = market_summary[col].astype(float)

market_summary['profit'] = market_summary['revenue'] - market_summary['cost']
market_summary['cost_per_piece'] = market_summary.apply(
    lambda row: row['cost'] / row['pieces'] if row['pieces'] > 0 else 0, axis=1
)
market_summary['margin_pct'] = market_summary.apply(
    lambda row: (row['profit'] / row['revenue'] * 100) if row['revenue'] > 0 else 0, axis=1
)

# Sort by revenue descending
market_summary = market_summary.sort_values('revenue', ascending=False)

# Sidebar - Market Selection
with st.sidebar:
    st.header("📍 Market Selection")

    selected_market = st.selectbox(
        "Select a market to view trends:",
        options=['All Markets'] + list(market_summary['market'].values),
        index=0
    )

    st.divider()

    # Summary stats
    st.metric("Total Markets", len(market_summary))
    st.metric("Total Revenue", f"${market_summary['revenue'].sum():,.0f}")
    st.metric("Total Profit", f"${market_summary['profit'].sum():,.0f}")

# Main content
if selected_market == 'All Markets':
    # Show market summary table
    st.subheader("📊 Market Summary - 2025 YTD")

    # Format the dataframe for display
    display_df = market_summary.copy()
    display_df['revenue'] = display_df['revenue'].apply(lambda x: f"${x:,.0f}")
    display_df['cost'] = display_df['cost'].apply(lambda x: f"${x:,.0f}")
    display_df['profit'] = display_df['profit'].apply(lambda x: f"${x:,.0f}")
    display_df['cost_per_piece'] = display_df['cost_per_piece'].apply(lambda x: f"${x:,.2f}")
    display_df['margin_pct'] = display_df['margin_pct'].apply(lambda x: f"{x:.1f}%")
    display_df.columns = ['Market', 'Shipments', 'Revenue', 'Cost', 'Pieces', 'Profit', 'Cost/Piece', 'Margin %']

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )

    # Top 10 markets chart
    st.subheader("📈 Top 10 Markets by Revenue")
    top_10 = market_summary.head(10)

    fig = px.bar(
        top_10,
        x='market',
        y=['revenue', 'cost'],
        barmode='group',
        labels={'value': 'Amount ($)', 'market': 'Market', 'variable': 'Type'},
        color_discrete_map={'revenue': '#2ecc71', 'cost': '#e74c3c'}
    )
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)

    # Profit by market
    st.subheader("💰 Profit by Market (Top 10)")
    fig2 = px.bar(
        top_10,
        x='market',
        y='profit',
        color='profit',
        color_continuous_scale=['#e74c3c', '#f39c12', '#2ecc71'],
        labels={'profit': 'Profit ($)', 'market': 'Market'}
    )
    fig2.update_layout(height=400)
    st.plotly_chart(fig2, use_container_width=True)

else:
    # Show market drill-down with quarterly trends
    st.subheader(f"📍 Market: {selected_market} - Quarterly Trends")

    # Filter data for selected market
    market_data = data[data['market'] == selected_market].copy()

    # Aggregate by quarter
    quarterly = market_data.groupby('quarter').agg({
        'shipments': 'sum',
        'revenue': 'sum',
        'cost': 'sum',
        'pieces': 'sum'
    }).reset_index()

    # Convert to float after aggregation
    for col in ['shipments', 'revenue', 'cost', 'pieces']:
        quarterly[col] = quarterly[col].astype(float)

    quarterly['profit'] = quarterly['revenue'] - quarterly['cost']
    quarterly['cost_per_piece'] = quarterly.apply(
        lambda row: row['cost'] / row['pieces'] if row['pieces'] > 0 else 0, axis=1
    )

    # Ensure quarters are in order
    quarter_order = ['Q1', 'Q2', 'Q3', 'Q4']
    quarterly['quarter'] = pd.Categorical(quarterly['quarter'], categories=quarter_order, ordered=True)
    quarterly = quarterly.sort_values('quarter')

    # Display metrics for this market
    col1, col2, col3, col4 = st.columns(4)
    market_totals = market_summary[market_summary['market'] == selected_market].iloc[0]

    with col1:
        st.metric("Total Revenue", f"${market_totals['revenue']:,.0f}")
    with col2:
        st.metric("Total Cost", f"${market_totals['cost']:,.0f}")
    with col3:
        st.metric("Total Profit", f"${market_totals['profit']:,.0f}")
    with col4:
        st.metric("Avg Cost/Piece", f"${market_totals['cost_per_piece']:,.2f}")

    st.divider()

    # Quarterly data table
    st.subheader("📋 Quarterly Breakdown")
    quarterly_display = quarterly.copy()
    quarterly_display['revenue'] = quarterly_display['revenue'].apply(lambda x: f"${x:,.0f}")
    quarterly_display['cost'] = quarterly_display['cost'].apply(lambda x: f"${x:,.0f}")
    quarterly_display['profit'] = quarterly_display['profit'].apply(lambda x: f"${x:,.0f}")
    quarterly_display['cost_per_piece'] = quarterly_display['cost_per_piece'].apply(lambda x: f"${x:,.2f}")
    quarterly_display.columns = ['Quarter', 'Shipments', 'Revenue', 'Cost', 'Pieces', 'Profit', 'Cost/Piece']

    st.dataframe(quarterly_display, use_container_width=True, hide_index=True)

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("💰 Profit Trend")
        fig_profit = go.Figure()
        fig_profit.add_trace(go.Scatter(
            x=quarterly['quarter'].astype(str),
            y=quarterly['profit'],
            mode='lines+markers',
            name='Profit',
            line=dict(color='#2ecc71', width=3),
            marker=dict(size=10)
        ))
        fig_profit.update_layout(
            xaxis_title="Quarter",
            yaxis_title="Profit ($)",
            height=350
        )
        st.plotly_chart(fig_profit, use_container_width=True)

    with col2:
        st.subheader("📦 Cost per Piece Trend")
        fig_cpp = go.Figure()
        fig_cpp.add_trace(go.Scatter(
            x=quarterly['quarter'].astype(str),
            y=quarterly['cost_per_piece'],
            mode='lines+markers',
            name='Cost/Piece',
            line=dict(color='#3498db', width=3),
            marker=dict(size=10)
        ))
        fig_cpp.update_layout(
            xaxis_title="Quarter",
            yaxis_title="Cost per Piece ($)",
            height=350
        )
        st.plotly_chart(fig_cpp, use_container_width=True)

    # Revenue vs Cost trend
    st.subheader("📊 Revenue vs Cost Trend")
    fig_rev_cost = go.Figure()
    fig_rev_cost.add_trace(go.Bar(
        x=quarterly['quarter'].astype(str),
        y=quarterly['revenue'],
        name='Revenue',
        marker_color='#2ecc71'
    ))
    fig_rev_cost.add_trace(go.Bar(
        x=quarterly['quarter'].astype(str),
        y=quarterly['cost'],
        name='Cost',
        marker_color='#e74c3c'
    ))
    fig_rev_cost.update_layout(
        barmode='group',
        xaxis_title="Quarter",
        yaxis_title="Amount ($)",
        height=400
    )
    st.plotly_chart(fig_rev_cost, use_container_width=True)

# --- TREND ANALYSIS SECTION ---
st.divider()
st.header("📈 Trend Analysis - Consistent Performers")

def calculate_market_trends(data):
    """Calculate linear regression trends for each market's quarterly data"""
    quarter_map = {'Q1': 1, 'Q2': 2, 'Q3': 3, 'Q4': 4}

    trends = []

    for market in data['market'].unique():
        market_data = data[data['market'] == market].copy()

        # Aggregate by quarter
        quarterly = market_data.groupby('quarter').agg({
            'revenue': 'sum',
            'cost': 'sum',
            'pieces': 'sum'
        }).reset_index()

        # Convert to float after aggregation
        for col in ['revenue', 'cost', 'pieces']:
            quarterly[col] = quarterly[col].astype(float)

        quarterly['profit'] = quarterly['revenue'] - quarterly['cost']
        quarterly['cost_per_piece'] = quarterly.apply(
            lambda row: row['cost'] / row['pieces'] if row['pieces'] > 0 else 0, axis=1
        )

        # Need at least 3 quarters of data for meaningful trend
        if len(quarterly) < 3:
            continue

        # Map quarters to numeric values
        quarterly['q_num'] = quarterly['quarter'].map(quarter_map)
        quarterly = quarterly.dropna(subset=['q_num'])

        if len(quarterly) < 3:
            continue

        # Calculate linear regression for profit
        x = quarterly['q_num'].values
        y_profit = quarterly['profit'].values
        y_cpp = quarterly['cost_per_piece'].values

        # Profit trend
        slope_profit, intercept, r_value, p_value, std_err = stats.linregress(x, y_profit)
        r2_profit = r_value ** 2

        # Cost per piece trend
        slope_cpp, intercept, r_value, p_value, std_err = stats.linregress(x, y_cpp)
        r2_cpp = r_value ** 2

        # Calculate totals for context
        total_revenue = quarterly['revenue'].sum()
        total_profit = quarterly['profit'].sum()
        avg_cpp = quarterly['cost_per_piece'].mean()

        trends.append({
            'market': market,
            'quarters': len(quarterly),
            'total_revenue': total_revenue,
            'total_profit': total_profit,
            'avg_cost_per_piece': avg_cpp,
            'profit_slope': slope_profit,
            'profit_r2': r2_profit,
            'cpp_slope': slope_cpp,
            'cpp_r2': r2_cpp
        })

    return pd.DataFrame(trends)

# Calculate trends
trends_df = calculate_market_trends(data)

if not trends_df.empty:
    # Filter for markets with enough data and meaningful trends
    min_r2 = 0.5  # Minimum R² for "consistent" trend

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🚀 Consistent Profit Growth")
        st.caption("Markets with steady quarter-over-quarter profit improvement")

        # Filter for positive slope and good R²
        profit_growers = trends_df[
            (trends_df['profit_slope'] > 0) &
            (trends_df['profit_r2'] >= min_r2)
        ].copy()
        profit_growers['consistency_score'] = profit_growers['profit_slope'] * profit_growers['profit_r2']
        profit_growers = profit_growers.sort_values('consistency_score', ascending=False).head(10)

        if not profit_growers.empty:
            display_profit = profit_growers[['market', 'profit_slope', 'profit_r2', 'total_profit']].copy()
            display_profit['profit_slope'] = display_profit['profit_slope'].apply(lambda x: f"${x:,.0f}/qtr")
            display_profit['profit_r2'] = display_profit['profit_r2'].apply(lambda x: f"{x:.2f}")
            display_profit['total_profit'] = display_profit['total_profit'].apply(lambda x: f"${x:,.0f}")
            display_profit.columns = ['Market', 'Growth Rate', 'R² (Consistency)', 'Total Profit']
            st.dataframe(display_profit, use_container_width=True, hide_index=True)
        else:
            st.info("No markets found with consistent profit growth (R² ≥ 0.5)")

    with col2:
        st.subheader("📉 Consistent Cost/Piece Reduction")
        st.caption("Markets with steady quarter-over-quarter cost efficiency gains")

        # Filter for negative slope (cost going down) and good R²
        cost_reducers = trends_df[
            (trends_df['cpp_slope'] < 0) &
            (trends_df['cpp_r2'] >= min_r2)
        ].copy()
        cost_reducers['consistency_score'] = abs(cost_reducers['cpp_slope']) * cost_reducers['cpp_r2']
        cost_reducers = cost_reducers.sort_values('consistency_score', ascending=False).head(10)

        if not cost_reducers.empty:
            display_cpp = cost_reducers[['market', 'cpp_slope', 'cpp_r2', 'avg_cost_per_piece']].copy()
            display_cpp['cpp_slope'] = display_cpp['cpp_slope'].apply(lambda x: f"${x:,.2f}/qtr")
            display_cpp['cpp_r2'] = display_cpp['cpp_r2'].apply(lambda x: f"{x:.2f}")
            display_cpp['avg_cost_per_piece'] = display_cpp['avg_cost_per_piece'].apply(lambda x: f"${x:,.2f}")
            display_cpp.columns = ['Market', 'Reduction Rate', 'R² (Consistency)', 'Avg Cost/Piece']
            st.dataframe(display_cpp, use_container_width=True, hide_index=True)
        else:
            st.info("No markets found with consistent cost/piece reduction (R² ≥ 0.5)")

    # Scatter plot showing all markets
    st.subheader("🎯 All Markets: Trend Strength vs Consistency")

    tab1, tab2 = st.tabs(["Profit Trends", "Cost/Piece Trends"])

    with tab1:
        fig_scatter_profit = px.scatter(
            trends_df,
            x='profit_r2',
            y='profit_slope',
            size='total_revenue',
            hover_name='market',
            hover_data={
                'profit_r2': ':.2f',
                'profit_slope': ':$,.0f',
                'total_revenue': ':$,.0f',
                'total_profit': ':$,.0f'
            },
            labels={
                'profit_r2': 'R² (Consistency)',
                'profit_slope': 'Profit Growth ($/quarter)',
                'total_revenue': 'Total Revenue'
            },
            title='Profit Trend: Growth Rate vs Consistency'
        )
        # Add quadrant lines
        fig_scatter_profit.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig_scatter_profit.add_vline(x=0.5, line_dash="dash", line_color="gray", opacity=0.5)
        # Add annotation for top-right quadrant
        fig_scatter_profit.add_annotation(
            x=0.85, y=fig_scatter_profit.data[0].y.max() * 0.9 if len(fig_scatter_profit.data[0].y) > 0 else 1000,
            text="⭐ Best: Growing & Consistent",
            showarrow=False,
            font=dict(size=10, color="green")
        )
        fig_scatter_profit.update_layout(height=500)
        st.plotly_chart(fig_scatter_profit, use_container_width=True)

    with tab2:
        fig_scatter_cpp = px.scatter(
            trends_df,
            x='cpp_r2',
            y='cpp_slope',
            size='total_revenue',
            hover_name='market',
            hover_data={
                'cpp_r2': ':.2f',
                'cpp_slope': ':$,.2f',
                'total_revenue': ':$,.0f',
                'avg_cost_per_piece': ':$,.2f'
            },
            labels={
                'cpp_r2': 'R² (Consistency)',
                'cpp_slope': 'Cost/Piece Change ($/quarter)',
                'total_revenue': 'Total Revenue'
            },
            title='Cost/Piece Trend: Change Rate vs Consistency'
        )
        # Add quadrant lines
        fig_scatter_cpp.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig_scatter_cpp.add_vline(x=0.5, line_dash="dash", line_color="gray", opacity=0.5)
        # Add annotation for bottom-right quadrant (negative slope = good)
        fig_scatter_cpp.add_annotation(
            x=0.85, y=fig_scatter_cpp.data[0].y.min() * 0.9 if len(fig_scatter_cpp.data[0].y) > 0 else -10,
            text="⭐ Best: Reducing & Consistent",
            showarrow=False,
            font=dict(size=10, color="green")
        )
        fig_scatter_cpp.update_layout(height=500)
        st.plotly_chart(fig_scatter_cpp, use_container_width=True)
else:
    st.warning("Not enough quarterly data to calculate trends.")

