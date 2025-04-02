# scripts/parse_transactions.py
import os
import pandas as pd
import yaml
from datetime import datetime
from collections import defaultdict

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

import re

def normalize_ticker(ticker):
    ticker = ticker.replace('/', '')
    if ticker.startswith('.'):
        ticker = ticker[1:]
    match = re.match(r'^([A-Z]{1,3})', ticker.upper())
    return match.group(1) if match else ticker

def detect_strategy_type(legs):
    print("DEBUG Strategy Detection:")
    for leg in legs:
        print(f"  - {leg['side']} {leg['type']} {leg['strike']} exp {leg['expiry']}")

    puts = [leg for leg in legs if leg['type'] == 'put']
    calls = [leg for leg in legs if leg['type'] == 'call']
    shorts = [leg for leg in legs if leg['side'] == 'short']
    longs = [leg for leg in legs if leg['side'] == 'long']

    expiries = list(set(leg['expiry'] for leg in legs))
    base_tickers = list(set(normalize_ticker(leg['ticker']) for leg in legs))

    # Cross-expiry Calendar 1-1-2 (e.g., /MESH5 + /MESM5)
    if len(legs) == 3 and len(puts) == 3 and len(shorts) == 2 and len(longs) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_puts = [leg for leg in longs if leg['type'] == 'put']
        if len(short_puts) == 2 and len(long_puts) == 1:
            short_strikes = set(leg['strike'] for leg in short_puts)
            short_expiries = set(leg['expiry'] for leg in short_puts)
            long_expiry = long_puts[0]['expiry']
            if len(short_strikes) == 1 and all(exp <= long_expiry for exp in short_expiries):
                return "Calendar 1-1-2"

    if len(legs) == 2 and len(shorts) == 2 and len(puts) == 1 and len(calls) == 1:
        return "Strangle"

    if len(legs) == 1 and shorts:
        return "Short Put" if puts else "Short Call"
    elif len(legs) == 2:
        if puts:
            return "Put Vertical" if len(shorts) == 1 else "Put Spread"
        elif calls:
            return "Call Vertical" if len(shorts) == 1 else "Call Spread"
    elif len(legs) == 4 and puts and calls:
        return "Iron Condor"
    elif len(legs) == 3 and len(shorts) == 2 and len(longs) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_puts = [leg for leg in longs if leg['type'] == 'put']
        if len(short_puts) == 2 and len(long_puts) == 1 and all(leg['expiry'] == short_puts[0]['expiry'] for leg in short_puts):
            return "Ratio Spread (1-1-2)"

    return "Unnamed"

def is_roll_candidate(rows):
    close_actions = [r for r in rows if 'CLOSE' in r['Action']]
    open_actions = [r for r in rows if 'OPEN' in r['Action']]
    return bool(close_actions) and bool(open_actions)

def generate_yaml_from_order(order_id, rows, existing_strategy_file=None, override_ticker=None):
    sample = rows[0]
    ticker = override_ticker if override_ticker else sample['Root Symbol']
    opened = pd.to_datetime(sample['Date']).strftime("%Y-%m-%d")

    legs = []
    gross_credit = 0
    total_fees = 0

    for row in rows:
        side = 'short' if 'SELL' in row['Action'] else 'long'
        leg = {
            'type': row['Call or Put'].lower(),
            'ticker': row['Root Symbol'],
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
    strategy_type = detect_strategy_type(legs)
    
    order_ids = sorted(set(int(row['Order #']) for row in rows if 'Order #' in row and not pd.isna(row['Order #'])))

    strategy = {
        'strategy': strategy_type,
        'ticker': ticker,
        'opened': opened,
        'status': 'open',
        'initial_credit': net_credit,
        'order_ids': order_ids,
        'legs': legs,
        'notes': '',
        'tags': []
    }

    if existing_strategy_file:
        path = os.path.join(STRATEGY_DIR, existing_strategy_file)
        with open(path, 'r') as f:
            existing = yaml.safe_load(f)

        existing['legs'].extend(legs)
        existing['initial_credit'] += net_credit
        existing['notes'] += f"\nRolled on {opened} (order #{order_id})"
        if 'tags' not in existing:
            existing['tags'] = []
        if 'rolled' not in existing['tags']:
            existing['tags'].append('rolled')
        existing['roll_count'] = existing.get('roll_count', 0) + 1
        existing['order_id'] = int(order_id)

        with open(path, 'w') as f:
            yaml.dump(existing, f)

        print(f"🔄 Updated existing strategy: {existing_strategy_file} (added legs from roll, +{net_credit} credit)")
        return

    filename = f"{ticker.lower()}_{opened}.yaml"
    path = os.path.join(STRATEGY_DIR, filename)
    with open(path, 'w') as f:
        yaml.dump(strategy, f)

    print(f"✅ Created strategy YAML: {filename} (strategy: {strategy_type}, net credit after fees: {net_credit})")

if __name__ == '__main__':
    ENABLE_MULTI_ORDER_DETECTION = True
    files = [f for f in os.listdir(TRANSACTIONS_DIR) if f.endswith(".csv")]
    for file in files:
        print(f"\n📄 Processing {file}...")
        df = load_transaction_file(os.path.join(TRANSACTIONS_DIR, file))

        if ENABLE_MULTI_ORDER_DETECTION:
            # Group trades by normalized ticker and date
            df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
            df['Underlying'] = df['Symbol'].apply(lambda s: normalize_ticker(s.split()[0].replace('/', '')))
            combo_groups = df.groupby(['Date', 'Underlying'])

            for (trade_date, base_underlying), rows in combo_groups:
                print(f"\n🧾 Combined strategy candidate on {base_underlying} for {trade_date}:")
                for _, row in rows.iterrows():
                    print(f" - {row['Action']}: {row['Call or Put']} {row['Strike Price']} exp {row['Expiration Date']} @ {row['Average Price']} | Fees: {row['Fees']}")

                confirm = input("Generate strategy YAML from this multi-order group? [y/N]: ").strip().lower()
                if confirm == 'y':
                    generate_yaml_from_order(order_id=0, rows=list(rows.to_dict(orient='records')), override_ticker=base_underlying.lower())
        else:
            grouped = group_by_order(df)
            for order_id, rows in grouped.items():
                sample = rows[0]
                print(f"\n🧾 Order #{int(order_id)} ({normalize_ticker(sample['Root Symbol'])})")
                for row in rows:
                    print(f" - {row['Action']}: {row['Call or Put']} {row['Strike Price']} exp {row['Expiration Date']} @ {row['Average Price']} | Fees: {row['Fees']}")

                if is_roll_candidate(rows):
                    print("⚠️  This order may represent a roll (close + open legs). You may want to link this to an existing strategy.")

                confirm = input("Generate strategy YAML from this order? [y/N]: ").strip().lower()
                if confirm == 'y' and is_roll_candidate(rows):
                    candidates = [f for f in os.listdir(STRATEGY_DIR) if f.endswith('.yaml')]
                    if candidates:
                        print("📂 Open strategies:")
                        for idx, fname in enumerate(candidates):
                            print(f" [{idx}] {fname}")
                        pick = input("🔁 Link this roll to which existing strategy? Enter index or press enter to skip: ").strip()
                        if pick.isdigit() and int(pick) < len(candidates):
                            generate_yaml_from_order(order_id, rows, existing_strategy_file=candidates[int(pick)])
                            continue
                if confirm == 'y':
                    generate_yaml_from_order(order_id, rows)

