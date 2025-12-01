import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import pytz # NEW: For Timezone conversion

# --- PAGE SETUP ---
st.set_page_config(page_title="Productivity Manager", layout="wide", page_icon="🚀")

# Load environment variables (for local testing)
load_dotenv()

# --- CONFIGURATION ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY"))

# --- SETTINGS ---
# Any idle block longer than this (minutes) is treated as "Offline/Went Home"
MAX_IDLE_THRESHOLD_MINS = 15 
# Timezone Setting
REPORT_TIMEZONE = 'America/Chicago' 

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("🚨 Configuration Error: Supabase URL or Key is missing.")
    st.stop()

@st.cache_resource
def init_connection():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Connection Error: {e}")
        st.stop()

supabase = init_connection()

# --- SIDEBAR FILTERS ---
st.sidebar.header("Filter Options")
# Get "Today" in Texas time, not Server time
today = datetime.now(pytz.timezone(REPORT_TIMEZONE)).date()
date_range = st.sidebar.date_input("Select Date Range", [today, today])

# --- LOAD DATA (Timezone Aware) ---
@st.cache_data(ttl=60)
def load_data(start_date, end_date):
    # 1. Define the Reporting Timezone (Texas)
    tz = pytz.timezone(REPORT_TIMEZONE)
    
    # 2. Convert selected dates to Texas Midnight (Start) and Texas End-of-Day (End)
    # This creates a "Timezone Aware" datetime object
    start_dt_loc = tz.localize(datetime.combine(start_date, datetime.min.time()))
    end_dt_loc = tz.localize(datetime.combine(end_date, datetime.max.time()))
    
    # 3. Convert those Texas times to UTC (because Supabase stores in UTC)
    start_utc_str = start_dt_loc.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc_str = end_dt_loc.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Query DB using the calculated UTC range
        response = supabase.table("logs").select("*")\
            .gte("created_at", start_utc_str)\
            .lte("created_at", end_utc_str)\
            .order("created_at", desc=False)\
            .execute()
    except Exception as e:
        st.error(f"Database Fetch Error: {e}")
        return pd.DataFrame()
        
    if not response.data:
        return pd.DataFrame()
        
    df = pd.DataFrame(response.data)
    
    # 4. Process the Timestamp Column
    # First, convert string to datetime (It will likely be UTC or naive)
    df['created_at'] = pd.to_datetime(df['created_at'])
    
    # Ensure it is UTC aware
    if df['created_at'].dt.tz is None:
        df['created_at'] = df['created_at'].dt.tz_localize('UTC')
    else:
        df['created_at'] = df['created_at'].dt.tz_convert('UTC')
    
    # Finally, convert the UTC column to Texas Time for display
    df['created_at'] = df['created_at'].dt.tz_convert(REPORT_TIMEZONE)
    
    # Create Formatted Date column (mm-dd-yyyy)
    df['Date'] = df['created_at'].dt.strftime('%m-%d-%Y')
    return df

if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
    df = load_data(start, end)
else:
    st.info("Please select a date range.")
    st.stop()

if df.empty:
    st.warning("No logs found for this date range.")
    st.stop()

all_users = df['user_name'].unique()
selected_users = st.sidebar.multiselect("Select Employees", all_users, default=all_users)
df = df[df['user_name'].isin(selected_users)]

if df.empty:
    st.warning("No logs found for selected users.")
    st.stop()

# --- LOGIC 1: CONDENSE SESSIONS ---
def condense_sessions(raw_df):
    if raw_df.empty: return pd.DataFrame()

    df_clean = raw_df.copy()
    df_clean['minute_bucket'] = df_clean['created_at'].dt.floor('min')

    minute_groups = df_clean.groupby(['user_name', 'minute_bucket'])
    
    minutes_data = []
    for (user, minute), group in minute_groups:
        try:
            if not group.empty:
                dominant_app = group['application'].mode()[0]
                dominant_status = group['status'].mode()[0]
                minutes_data.append({
                    'User': user,
                    'Start': minute,
                    'End': minute + timedelta(minutes=1),
                    'App': dominant_app,
                    'Status': dominant_status,
                    'Duration_Mins': 1.0
                })
        except IndexError:
            continue
            
    if not minutes_data: return pd.DataFrame()
    
    return pd.DataFrame(minutes_data)

# --- LOGIC 2: SMART FILTERING & AGGREGATION ---
def process_productivity(session_df):
    if session_df.empty: return pd.DataFrame(), pd.DataFrame()
    
    df = session_df.sort_values(by=['User', 'Start'])
    
    sessions = []
    current_session = None

    for _, row in df.iterrows():
        if current_session is None:
            current_session = row.to_dict()
            continue
            
        time_gap = (row['Start'] - current_session['End']).total_seconds()
        
        if (row['User'] == current_session['User'] and 
            row['App'] == current_session['App'] and 
            row['Status'] == current_session['Status'] and
            time_gap <= 60): 
            
            current_session['End'] = row['End']
            current_session['Duration_Mins'] += row['Duration_Mins']
        else:
            sessions.append(current_session)
            current_session = row.to_dict()
            
    if current_session:
        sessions.append(current_session)
        
    merged_df = pd.DataFrame(sessions)
    
    # Filter out long idle blocks
    valid_df = merged_df[~((merged_df['Status'] == 'Idle') & (merged_df['Duration_Mins'] > MAX_IDLE_THRESHOLD_MINS))].copy()
    
    # Create Hourly View
    valid_df['Hour'] = valid_df['Start'].dt.strftime('%H:00')
    valid_df['Date'] = valid_df['Start'].dt.strftime('%m-%d-%Y') # Format mm-dd-yyyy
    
    valid_df['Active_Mins'] = valid_df.apply(lambda x: x['Duration_Mins'] if x['Status'] == 'Active' else 0, axis=1)
    valid_df['Idle_Mins'] = valid_df.apply(lambda x: x['Duration_Mins'] if x['Status'] == 'Idle' else 0, axis=1)
    
    grouped_hourly = valid_df.groupby(['User', 'Date', 'Hour', 'App'])[['Active_Mins', 'Idle_Mins']].sum().reset_index()
    grouped_hourly = grouped_hourly.sort_values(by=['User', 'Date', 'Hour'])
    
    return valid_df, grouped_hourly

# Process Data
session_df = condense_sessions(df)
valid_sessions, hourly_df = process_productivity(session_df)

# --- DASHBOARD UI ---
st.title("📊 Organized Productivity Report")
st.caption(f"Timezone: {REPORT_TIMEZONE} | Idle Threshold: >{MAX_IDLE_THRESHOLD_MINS} mins filtered out.")
st.markdown("---")

if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# KPI Calculation
if not valid_sessions.empty:
    total_active_mins = valid_sessions[valid_sessions['Status'] == 'Active']['Duration_Mins'].sum()
    total_idle_mins = valid_sessions[valid_sessions['Status'] == 'Idle']['Duration_Mins'].sum()
    
    total_hours = round((total_active_mins + total_idle_mins) / 60, 1)
    
    if total_hours > 0:
        prod_score = round((total_active_mins / (total_active_mins + total_idle_mins) * 100), 1)
    else:
        prod_score = 0
else:
    total_hours = 0
    prod_score = 0
    total_active_mins = 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("👥 Employees", len(selected_users))
c2.metric("⏱ Total Shift Time", f"{total_hours} hrs")
c3.metric("⚡ Productive", f"{round(total_active_mins/60, 1)} hrs")
c4.metric("📈 Focus Score", f"{prod_score}%")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("🏆 Top Applications")
    if not valid_sessions.empty:
        app_summary = valid_sessions[valid_sessions['Status'] == 'Active'].groupby('App')['Duration_Mins'].sum().reset_index()
        app_summary = app_summary.sort_values(by='Duration_Mins', ascending=False).head(10)
        
        if not app_summary.empty:
            fig = px.bar(app_summary, x='Duration_Mins', y='App', orientation='h', title="Active Mins", color='Duration_Mins')
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No active sessions found.")

with col_right:
    st.subheader("⏳ Active vs Idle")
    if not valid_sessions.empty:
        status_summary = valid_sessions.groupby('Status')['Duration_Mins'].sum().reset_index()
        fig_pie = px.pie(status_summary, values='Duration_Mins', names='Status', color='Status', 
                         color_discrete_map={'Active':'#00CC96', 'Idle':'#EF553B'})
        st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("---")
st.subheader("📅 Hourly Application Summary")

if not hourly_df.empty:
    view_user = st.selectbox("Select User", selected_users)
    user_hourly = hourly_df[hourly_df['User'] == view_user]
    
    display_df = user_hourly[['Date', 'Hour', 'App', 'Active_Mins', 'Idle_Mins']].rename(columns={
        'Active_Mins': 'Active Time (mins)',
        'Idle_Mins': 'Idle Time (mins)'
    })
    
    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("No data available to display.")