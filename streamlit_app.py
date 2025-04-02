# streamlit_app.py
import os
import yaml
import pandas as pd
import streamlit as st
from datetime import datetime

# Paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Load strategies from directory
def load_strategies(dir_path):
    strategies = []
    for file in os.listdir(dir_path):
        if file.endswith(".yaml"):
            with open(os.path.join(dir_path, file)) as f:
                yml = yaml.safe_load(f)
                yml['file'] = file
                yml['source'] = os.path.basename(dir_path)
                strategies.append(yml)
    return pd.DataFrame(strategies)

# Load daily log for a strategy
def load_log(strategy_file):
    csv_file = strategy_file.replace(".yaml", ".csv")
    csv_path = os.path.join(LOG_DIR, csv_file)
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path, parse_dates=['Date'])
        return df
    return None

# Page layout
st.set_page_config(layout="wide", page_title="Options Tracker")
st.title("📘 Options Trading Journal")

# Load strategies
open_df = load_strategies(STRATEGY_DIR)
closed_df = load_strategies(ARCHIVE_DIR)
all_df = pd.concat([open_df, closed_df], ignore_index=True)

# Sidebar filters
st.sidebar.header("🔎 Filter Strategies")
tickers = sorted(all_df['ticker'].dropna().unique())
strategies = sorted(all_df['strategy'].dropna().unique())
status = st.sidebar.radio("Status", ["All", "Open", "Closed"], index=0)
ticker_filter = st.sidebar.multiselect("Ticker", tickers)
strategy_filter = st.sidebar.multiselect("Strategy Type", strategies)

# Apply filters
filtered_df = all_df.copy()
if status != "All":
    filtered_df = filtered_df[filtered_df['status'] == status.lower()]
if ticker_filter:
    filtered_df = filtered_df[filtered_df['ticker'].isin(ticker_filter)]
if strategy_filter:
    filtered_df = filtered_df[filtered_df['strategy'].isin(strategy_filter)]

# Main table
st.subheader("📂 Strategy List")
display_cols = [c for c in ["ticker", "strategy", "status", "opened", "closed", "initial_credit", "realized_pnl", "file"] if c in filtered_df.columns]

st.dataframe(
    filtered_df[display_cols]
    .fillna("-"),
    use_container_width=True
)

# Detail view
st.subheader("🔍 Strategy Detail")
selected = st.selectbox("Select a strategy to view details:", filtered_df['file'])
strategy = filtered_df[filtered_df['file'] == selected].iloc[0]
st.markdown(f"### {strategy['ticker']} - {strategy['strategy']}")
st.markdown(f"**Opened:** {strategy['opened']}  ")
if 'closed' in strategy:
    st.markdown(f"**Closed:** {strategy['closed']}  ")
if 'initial_credit' in strategy:
    st.markdown(f"**Initial Credit:** ${strategy['initial_credit']:.2f}  ")
if 'realized_pnl' in strategy:
    st.markdown(f"**Realized PnL:** ${strategy['realized_pnl']:.2f}  ")
if 'roll_count' in strategy:
    st.markdown(f"**Roll Count:** {strategy['roll_count']}")
if strategy.get("tags"):
    st.markdown(f"**Tags:** {', '.join(strategy['tags'])}")

# Legs
st.markdown("#### Legs")
st.table(pd.DataFrame(strategy['legs']))

# Log chart
log_df = load_log(selected)
if log_df is not None:
    st.markdown("#### 📈 Daily Log")
    metric = st.selectbox("Choose metric to plot:", [col for col in log_df.columns if col != "Date"])
    st.line_chart(log_df.set_index("Date")[metric])
else:
    st.info("No daily log data found for this strategy.")

