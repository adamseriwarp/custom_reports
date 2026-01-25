import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv
import streamlit as st

# Load environment variables (for local development)
load_dotenv()

def get_secret(key, default=None):
    """
    Get a secret from Streamlit Cloud secrets or environment variables.
    Streamlit Cloud uses st.secrets, local dev uses .env
    """
    # Try Streamlit secrets first (for cloud deployment)
    try:
        if hasattr(st, 'secrets') and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    # Fall back to environment variables (for local development)
    return os.getenv(key, default)

@st.cache_resource
def get_db_connection():
    """
    Create and return a MySQL database connection.
    Uses st.cache_resource to maintain a single connection across reruns.
    """
    try:
        connection = mysql.connector.connect(
            host=get_secret('DB_HOST'),
            port=int(get_secret('DB_PORT', 3306)),
            user=get_secret('DB_USER'),
            password=get_secret('DB_PASSWORD'),
            database=get_secret('DB_NAME')
        )

        if connection.is_connected():
            return connection
    except Error as e:
        st.error(f"Error connecting to MySQL database: {e}")
        return None

def execute_query(query, params=None):
    """
    Execute a SQL query and return results as a pandas DataFrame.
    
    Args:
        query (str): SQL query to execute
        params (tuple, optional): Parameters for parameterized queries
    
    Returns:
        pandas.DataFrame: Query results
    """
    connection = get_db_connection()
    
    if connection is None:
        st.error("Failed to connect to database")
        return None
    
    try:
        cursor = connection.cursor(dictionary=True)
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        results = cursor.fetchall()
        cursor.close()
        
        import pandas as pd
        return pd.DataFrame(results)
    
    except Error as e:
        st.error(f"Error executing query: {e}")
        return None

def test_connection():
    """
    Test the database connection and return connection status.
    
    Returns:
        bool: True if connection successful, False otherwise
    """
    connection = get_db_connection()
    
    if connection and connection.is_connected():
        db_info = connection.get_server_info()
        return True, f"Successfully connected to MySQL Server version {db_info}"
    else:
        return False, "Failed to connect to database"

