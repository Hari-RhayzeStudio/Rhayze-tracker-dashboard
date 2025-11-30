import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load environment variables for local testing only. 
# Streamlit Cloud ignores this and uses st.secrets.

load_dotenv() 

# --- CONFIGURATION (SECURE CREDENTIALS) ---
# 1. Attempt to retrieve keys from Streamlit Secrets (for deployment)
# 2. Fallback to os.getenv (for local development with a .env file)

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY"))

# --- INITIAL VALIDATION CHECK ---
if not SUPABASE_URL or not SUPABASE_KEY:
    st.set_page_config(page_title="Productivity Manager", layout="wide")
    st.error("?? Configuration Error: Supabase URL or Key is missing.")
    st.warning("Please ensure your keys are set in your `.streamlit/secrets.toml` file (for deployment) or your local `.env` file.")
    st.stop()
# --- End CONFIGURATION ---


# --- PAGE SETUP ---
st.set_page_config(page_title="Productivity Manager", layout="wide")

# --- CONNECT TO DATABASE ---
@st.cache_resource
def init_connection():
    """Initializes and caches the Supabase connection."""
    # Use the securely loaded keys
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()


# --- LOAD DATA FUNCTION ---
@st.cache_data(ttl=60) # Cache data for 60 seconds to reduce DB load

def load_data():
    """Fetches and processes activity logs from Supabase."""

    try:
        # Fetch up to 5000 logs
        response = supabase.table("logs").select("*").order("created_at", desc=True).limit(5000).execute()
    except Exception as e:
        st.error(f"Database Fetch Error: {e}")
        return pd.DataFrame()

    if not response.data:
        return pd.DataFrame()

    df = pd.DataFrame(response.data)

    # Process Timestamps
    df['created_at'] = pd.to_datetime(df['created_at'])
    df['Date'] = df['created_at'].dt.date
    df['Hour'] = df['created_at'].dt.hour
    return df

# --- UI LAYOUT ---
st.title("?? Organization Productivity Dashboard")
st.markdown("Real-time analysis of employee activity on Virtual Machines.")

# Refresh Button
if st.button("?? Refresh Data"):
    st.cache_data.clear() # Clear all cached data
    st.rerun()

df = load_data()

if df.empty:
    st.warning("No data found yet. Start the employee agent on a VM.")
    st.stop()

# --- SIDEBAR FILTERS ---
st.sidebar.header("Filter Options")

# User Filter
all_users = df['user_name'].unique()
selected_users = st.sidebar.multiselect("Select Employees", all_users, default=all_users)

# Date Filter
min_date = df['Date'].min()
max_date = df['Date'].max()
date_range = st.sidebar.date_input("Select Date Range", [min_date, max_date])


# Apply Filters

if len(date_range) == 2:
    mask = (df['user_name'].isin(selected_users)) & \
           (df['Date'] >= date_range[0]) & \
           (df['Date'] <= date_range[1])
    filtered_df = df.loc[mask]
else:
    filtered_df = df.copy() # Use a copy if no range selected



# Check for empty filter result

if filtered_df.empty:
    st.warning("No data matches the current filters.")
    st.stop()

# --- KPI CARDS ---
total_logs = len(filtered_df)

# Ensure 'status' column exists and filter based on 'Active' and 'Idle'
active_logs = len(filtered_df[filtered_df['status'] == 'Active'])
idle_logs = len(filtered_df[filtered_df['status'] == 'Idle'])

# Assuming 1 log = 2 seconds (due to agent reporting interval)
est_hours = round((active_logs * 2) / 3600, 2)
productivity_score = round((active_logs / total_logs * 100), 1) if total_logs > 0 else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("?? Active Employees", len(filtered_df['user_name'].unique()))
col2.metric("? Productive Hours", f"{est_hours} hrs")
col3.metric("?? Idle Events", idle_logs)
col4.metric("?? Focus Score", f"{productivity_score}%")

st.markdown("---")

# --- VISUALIZATIONS ---
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("?? Top Applications")
    # Filter for active time only
    active_apps_df = filtered_df[filtered_df['status'] == 'Active'].copy()

    # Calculate usage and limit to top 10
    app_counts = active_apps_df['application'].value_counts().head(10).reset_index()
    app_counts.columns = ['Application', 'Usage Count']

    fig_bar = px.bar(app_counts, x='Usage Count', y='Application', orientation='h', color='Usage Count',
                     labels={'Usage Count': 'Active Log Count', 'Application': 'Application Name'},
                     title="Usage Frequency (Active Sessions)")
    st.plotly_chart(fig_bar, use_container_width=True)

with col_right:
    st.subheader("? Active vs Idle Ratio")

    # Ensure 'status' column exists
    status_counts = filtered_df['status'].value_counts().reset_index()
    status_counts.columns = ['Status', 'Count']

    fig_pie = px.pie(status_counts, names='Status', values='Count', color='Status', 
                     color_discrete_map={'Active':'#00CC96', 'Idle':'#EF553B'},
                     title="Percentage of Total Time Logs")
    st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("---")


# --- DETAILED LOGS ---
with st.expander("?? View Detailed Activity Logs"):
    st.dataframe(filtered_df[['created_at', 'user_name', 'status', 'application']].sort_values(by='created_at', ascending=False), use_container_width=True)