import streamlit as st
from auth import check_password

st.set_page_config(
    page_title="Profit by Lane Dashboard",
    page_icon="📊",
    layout="wide"
)

if not check_password():
    st.stop()

# Redirect to Summary View
st.switch_page("pages/0_Summary_View.py")