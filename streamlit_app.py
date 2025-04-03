# streamlit_app.py
import streamlit as st
import os
import yaml
import pandas as pd

STRATEGY_DIR = os.path.join(os.path.dirname(__file__), 'strategies')
ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), 'archive')

def load_strategies():
    strategies = []
    for folder in [STRATEGY_DIR, ARCHIVE_DIR]:
        for fname in os.listdir(folder):
            if fname.endswith(".yaml"):
                with open(os.path.join(folder, fname), 'r') as f:
                    data = yaml.safe_load(f)
                    data['file'] = fname
                    data['folder'] = os.path.basename(folder)
                    strategies.append(data)
    return pd.DataFrame(strategies)

def get_status_label(row):
    if row['status'] == 'closed':
        return 'Closed'
    elif any('status' in leg and leg['status'] == 'expired' for leg in row['legs']):
        return 'Partially Expired'
    elif 'rolled' in row.get('tags', []):
        return 'Rolled'
    return 'Open'

st.set_page_config(page_title="Options Tracker", layout="wide")
st.title("📈 Options Strategy Tracker")

with st.sidebar:
    st.header("🔍 Filters")
    df = load_strategies()
    df['status_label'] = df.apply(get_status_label, axis=1)

    status_filter = st.multiselect("Status", options=df['status_label'].unique(), default=list(df['status_label'].unique()))
    strategy_filter = st.multiselect("Strategy Type", options=df['strategy'].unique(), default=list(df['strategy'].unique()))
    ticker_filter = st.multiselect("Ticker", options=df['ticker'].unique(), default=list(df['ticker'].unique()))

filtered_df = df[
    df['status_label'].isin(status_filter) &
    df['strategy'].isin(strategy_filter) &
    df['ticker'].isin(ticker_filter)
]

st.markdown(f"### Showing {len(filtered_df)} strategies")

for _, row in filtered_df.iterrows():
    with st.expander(f"[{row['status_label']}] {row['strategy']} - {row['ticker']} ({row['opened']}) [{row['file']}]"):
        st.markdown(f"**Status:** {row['status_label']}")
        st.markdown(f"**Opened:** {row['opened']}")
        if 'closed' in row:
            st.markdown(f"**Closed:** {row['closed']}")
        st.markdown(f"**Initial Credit:** ${row['initial_credit']:.2f}")
        st.markdown(f"**Roll Count:** {row.get('roll_count', 0)}")
        st.markdown(f"**Tags:** {', '.join(row.get('tags', [])) or 'None'}")

        st.markdown("---")
        st.markdown("**Legs:**")
        legs_df = pd.DataFrame(row['legs'])
        if not legs_df.empty:
            if 'status' not in legs_df.columns:
                legs_df['status'] = 'active'
            legs_df = legs_df[['side', 'type', 'contracts', 'strike', 'expiry', 'status']]
            st.dataframe(legs_df, use_container_width=True)

        st.markdown("---")
        st.markdown("**Notes:**")
        st.code(row.get('notes', ''), language='markdown')

