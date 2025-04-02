# scripts/analyze_performance.py
import os
import yaml
import pandas as pd
from datetime import datetime
from collections import defaultdict

# Base directories
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

# Load all closed YAML strategies from archive/
def load_closed_strategies():
    data = []
    for file in os.listdir(ARCHIVE_DIR):
        if file.endswith(".yaml"):
            with open(os.path.join(ARCHIVE_DIR, file)) as f:
                yml = yaml.safe_load(f)
                if 'closed' in yml:
                    opened = datetime.strptime(yml['opened'], "%Y-%m-%d")
                    closed = datetime.strptime(yml['closed'], "%Y-%m-%d")
                    hold_days = (closed - opened).days
                    data.append({
                        'ticker': yml['ticker'],
                        'strategy': yml['strategy'],
                        'tags': yml.get('tags', []),
                        'opened': yml['opened'],
                        'closed': yml['closed'],
                        'hold_days': hold_days,
                        'pnl': yml.get('realized_pnl', 0),
                        'rolled': 'rolled' in yml.get('tags', []),
                        'roll_count': yml.get('roll_count', 0)
                    })
    return pd.DataFrame(data)


def summarize(df):
    total = len(df)
    winners = df[df['pnl'] > 0]
    losers = df[df['pnl'] <= 0]

    print("\n📊 Overall Summary")
    print("-------------------")
    print(f"Total trades: {total}")
    print(f"Win rate: {len(winners) / total * 100:.1f}%")
    print(f"Average hold time: {df['hold_days'].mean():.1f} days")
    print(f"Total PnL: ${df['pnl'].sum():.2f}")
    print(f"Average PnL per trade: ${df['pnl'].mean():.2f}")

    print("\n📂 By Strategy Type")
    print(df.groupby('strategy')['pnl'].agg(['count', 'sum', 'mean']))

    print("\n🏷️  By Tag")
    tag_counts = defaultdict(list)
    for _, row in df.iterrows():
        for tag in row['tags']:
            tag_counts[tag].append(row['pnl'])
    for tag, pnls in tag_counts.items():
        print(f" - {tag:<10}: count={len(pnls):<3}  avg_pnl=${sum(pnls)/len(pnls):.2f}  total=${sum(pnls):.2f}")

    print("\n📈 By Ticker")
    print(df.groupby('ticker')['pnl'].agg(['count', 'sum', 'mean']))


if __name__ == '__main__':
    df = load_closed_strategies()
    if df.empty:
        print("⚠️  No closed strategies found in archive/")
    else:
        summarize(df)

