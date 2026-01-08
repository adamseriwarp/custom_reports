import os
import pymysql
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Load environment variables (for local development)
load_dotenv()

def get_connection():
    """Create and return a MySQL connection."""
    # Try Streamlit secrets first (for deployed app), fall back to env vars (for local dev)
    try:
        host = st.secrets["MYSQL_HOST"]
        port = int(st.secrets.get("MYSQL_PORT", 3306))
        user = st.secrets["MYSQL_USER"]
        password = st.secrets["MYSQL_PASSWORD"]
        database = st.secrets["MYSQL_DATABASE"]
    except Exception:
        host = os.getenv('MYSQL_HOST')
        port = int(os.getenv('MYSQL_PORT', 3306))
        user = os.getenv('MYSQL_USER')
        password = os.getenv('MYSQL_PASSWORD')
        database = os.getenv('MYSQL_DATABASE')

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database
    )

def query_to_dataframe(sql: str) -> pd.DataFrame:
    """Execute a SQL query and return results as a pandas DataFrame."""
    conn = get_connection()
    try:
        df = pd.read_sql(sql, conn)
        return df
    finally:
        conn.close()

