# scripts/parse_transactions.py
import os
import pandas as pd
import yaml
from datetime import datetime
from collections import defaultdict
import shutil
import re

# Resolve project paths relative to this script's location
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRANSACTIONS_DIR = os.path.join(BASE_DIR, "transactions")
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")


def load_transaction_file(filepath):
    df = pd.read_csv(filepath)
    df = df[df['Type'] == 'Trade']
    df = df[df['Instrument Type'].isin(['Equity Option', 'Future Option'])]
    return df

def group_by_order(df):
    grouped = defaultdict(list)
    for _, row in df.iterrows():
        grouped[row['Order #']].append(row)
    return grouped

def normalize_ticker(ticker):
    ticker = ticker.replace('/', '')
    if ticker.startswith('.'):
        ticker = ticker[1:]
    match = re.match(r'^([A-Z]{1,3})', ticker.upper())
    return match.group(1) if match else ticker

def is_close_order(rows):
    return all('CLOSE' in r['Action'] for r in rows)

def match_close_candidate(rows, strategy):
    closing_legs = [
        {
            'type': row['Call or Put'].lower(),
            'strike': float(row['Strike Price']),
            'expiry': pd.to_datetime(row['Expiration Date']).strftime("%Y-%m-%d"),
            'side': 'short' if 'BUY_TO_CLOSE' in row['Action'] else 'long'
        }
        for row in rows
    ]
    active_legs = [
        {
            'type': leg['type'],
            'strike': leg['strike'],
            'expiry': leg['expiry'],
            'side': leg['side']
        }
        for leg in strategy['legs']
    ]
    return all(closing_leg in active_legs for closing_leg in closing_legs)

def calculate_realized_pnl(strategy, rows):
    pnl = 0.0
    for row in rows:
        pnl += float(row['Value']) - float(row['Fees'])
    return round(pnl, 2)

def close_strategy(strategy_file, rows):
    path = os.path.join(STRATEGY_DIR, strategy_file)
    with open(path, 'r') as f:
        strategy = yaml.safe_load(f)
    strategy['status'] = 'closed'
    strategy['closed'] = pd.to_datetime(rows[0]['Date']).strftime('%Y-%m-%d')
    strategy['realized_pnl'] = calculate_realized_pnl(strategy, rows)
    strategy['notes'] = strategy.get('notes', '') + f"\nClosed on {strategy['closed']}"
    archive_path = os.path.join(ARCHIVE_DIR, strategy_file)
    with open(archive_path, 'w') as f:
        yaml.dump(strategy, f)
    os.remove(path)
    print(f"📦 Strategy closed and archived: {strategy_file} (PnL: {strategy['realized_pnl']})")

if __name__ == '__main__':
    ENABLE_MULTI_ORDER_DETECTION = True
    files = [f for f in os.listdir(TRANSACTIONS_DIR) if f.endswith(".csv")]
    for file in files:
        print(f"\n📄 Processing {file}...")
        df = load_transaction_file(os.path.join(TRANSACTIONS_DIR, file))

        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
        df['Underlying'] = df['Symbol'].apply(lambda s: normalize_ticker(s.split()[0].replace('/', '')))
        combo_groups = df.groupby(['Date', 'Underlying'])

        for (trade_date, base_underlying), rows in combo_groups:
            print(f"\n🧾 Combined strategy candidate on {base_underlying} for {trade_date}:")
            for _, row in rows.iterrows():
                print(f" - {row['Action']}: {row['Call or Put']} {row['Strike Price']} exp {row['Expiration Date']} @ {row['Average Price']} | Fees: {row['Fees']}")

            confirm = input("Generate strategy YAML from this multi-order group? [y/N]: ").strip().lower()

            if confirm == 'y':
                order_ids = sorted(set(int(r['Order #']) for _, r in rows.iterrows() if 'Order #' in r and not pd.isna(r['Order #'])))
                ticker = base_underlying.lower()
                opened = trade_date
                legs = []
                gross_credit = 0
                total_fees = 0
                for _, row in rows.iterrows():
                    side = 'short' if 'SELL' in row['Action'] else 'long'
                    leg = {
                        'type': row['Call or Put'].lower(),
                        'ticker': row['Root Symbol'].strip().replace('./', ''),
                        'side': side,
                        'strike': float(row['Strike Price']),
                        'expiry': pd.to_datetime(row['Expiration Date']).strftime("%Y-%m-%d"),
                        'contracts': int(row['Quantity']),
                        'entry_price': abs(float(row['Average Price']) / 100)
                    }
                    legs.append(leg)
                    gross_credit += float(row['Value'])
                    total_fees += float(row['Fees'])
                net_credit = round(abs(gross_credit) - abs(total_fees), 2)

                # Detect strategy type (simplified)
                puts = [leg for leg in legs if leg['type'] == 'put']
                shorts = [leg for leg in legs if leg['side'] == 'short']
                longs = [leg for leg in legs if leg['side'] == 'long']
                strategy_type = "Unnamed"
                if len(legs) == 3 and len(puts) == 3 and len(shorts) == 2 and len(longs) == 1:
                    short_puts = [leg for leg in shorts if leg['type'] == 'put']
                    long_puts = [leg for leg in longs if leg['type'] == 'put']
                    short_strikes = set(leg['strike'] for leg in short_puts)
                    short_expiries = set(leg['expiry'] for leg in short_puts)
                    long_expiry = long_puts[0]['expiry']
                    if len(short_strikes) == 1 and all(exp <= long_expiry for exp in short_expiries):
                        strategy_type = "Calendar 1-1-2"
                elif len(legs) == 1 and len(shorts) == 1:
                    strategy_type = "Short Put" if puts else "Short Call"

                strategy = {
                    'strategy': strategy_type,
                    'ticker': ticker,
                    'opened': opened,
                    'status': 'open',
                    'initial_credit': net_credit,
                    'order_ids': order_ids,
                    'legs': legs,
                    'notes': '',
                    'tags': [],
                    'roll_count': 0
                }

                filename = f"{ticker}_{opened}.yaml"
                path = os.path.join(STRATEGY_DIR, filename)
                with open(path, 'w') as f:
                    yaml.dump(strategy, f)

                print(f"✅ Created strategy YAML: {filename} (strategy: {strategy_type}, net credit after fees: {net_credit})")

            elif is_close_order(rows.to_dict(orient='records')):
                candidates = [f for f in os.listdir(STRATEGY_DIR) if f.endswith('.yaml')]
                for fname in candidates:
                    with open(os.path.join(STRATEGY_DIR, fname), 'r') as f:
                        strategy = yaml.safe_load(f)
                    if match_close_candidate(rows.to_dict(orient='records'), strategy):
                        print(f"🛑 This order may represent a close of strategy: {fname}")
                        close_confirm = input(f"Mark {fname} as closed? [y/N]: ").strip().lower()
                        if close_confirm == 'y':
                            close_strategy(fname, rows.to_dict(orient='records'))
                            break

