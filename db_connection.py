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

def list_tables() -> pd.DataFrame:
    """List all tables in the database."""
    return query_to_dataframe("SHOW TABLES")

def describe_table(table_name: str) -> pd.DataFrame:
    """Get the schema of a specific table."""
    return query_to_dataframe(f"DESCRIBE `{table_name}`")


if __name__ == "__main__":
    # Test connection and list tables
    print("Connecting to MySQL database...")
    try:
        tables = list_tables()
        print(f"\nFound {len(tables)} tables:\n")
        print(tables.to_string())
    except Exception as e:
        print(f"Error: {e}")

