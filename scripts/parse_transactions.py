# scripts/parse_transactions.py
import os
import pandas as pd
import yaml
from datetime import datetime
from collections import defaultdict
import shutil
import re

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRANSACTIONS_DIR = os.path.join(BASE_DIR, "transactions")
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

def detect_strategy_type(legs):
    puts = [leg for leg in legs if leg['type'] == 'put']
    calls = [leg for leg in legs if leg['type'] == 'call']
    shorts = [leg for leg in legs if leg['side'] == 'short']
    longs = [leg for leg in legs if leg['side'] == 'long']

    # Calendar 1-1-2 (1 debit spread + 2 short-term puts)
    if len(puts) == 3 and len(shorts) == 2 and len(longs) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_put = next((leg for leg in longs if leg['type'] == 'put'), None)
        if long_put:
            long_expiry = long_put['expiry']
            debit_spread_short = next((leg for leg in short_puts if leg['expiry'] == long_expiry), None)
            near_term_puts = [leg for leg in short_puts if leg['expiry'] != long_expiry]
            if debit_spread_short and len(near_term_puts) == 1:
                return "Calendar 1-1-2"

    if len(legs) == 1 and shorts:
        return "Short Put" if puts else "Short Call"

    return "Unnamed"

def normalize_ticker(ticker):
    ticker = ticker.replace('/', '').strip()
    if ticker.startswith('.'):
        ticker = ticker[1:]
    match = re.match(r'^([A-Z]{1,3})', ticker.upper())
    return match.group(1) if match else ticker

def load_transaction_file(filepath):
    df = pd.read_csv(filepath)
    df = df[(df['Type'] == 'Trade') | ((df['Type'] == 'Receive Deliver') & (df['Sub Type'] == 'Expiration'))]
    df = df[df['Instrument Type'].isin(['Equity Option', 'Future Option'])]
    return df

def generate_yaml_from_order(order_id, rows, existing_strategy_file=None, override_ticker=None):
    sample = rows[0]
    ticker = override_ticker if override_ticker else sample['Root Symbol'].strip()
    opened = pd.to_datetime(sample['Date']).strftime("%Y-%m-%d")

    legs = []
    gross_credit = 0
    total_fees = 0

    for row in rows:
        side = 'short' if 'SELL' in row['Action'] else 'long'
        leg = {
            'type': row['Call or Put'].lower(),
            'ticker': row['Root Symbol'].strip(),
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

    strategy = {
        'strategy': strategy_type,
        'ticker': normalize_ticker(ticker),
        'opened': opened,
        'status': 'open',
        'initial_credit': net_credit,
        'order_ids': [int(order_id)] if order_id else [],
        'legs': legs,
        'notes': '',
        'tags': [],
        'roll_count': 0
    }

    if existing_strategy_file:
        path = os.path.join(STRATEGY_DIR, existing_strategy_file)
        with open(path, 'r') as f:
            existing = yaml.safe_load(f)

        existing['legs'].extend(legs)
        existing['initial_credit'] += net_credit
        existing['notes'] += f"\nRolled on {opened} (order #{order_id})"
        if 'rolled' not in existing['tags']:
            existing['tags'].append('rolled')
        existing['roll_count'] = existing.get('roll_count', 0) + 1
        existing['order_ids'] = list(set(existing.get('order_ids', []) + [int(order_id)]))

        with open(path, 'w') as f:
            yaml.dump(existing, f)

        print(f"🔄 Updated existing strategy: {existing_strategy_file} (added legs from roll, +{net_credit} credit)")
        return

    filename = f"{normalize_ticker(ticker).lower()}_{opened}.yaml"
    path = os.path.join(STRATEGY_DIR, filename)
    with open(path, 'w') as f:
        yaml.dump(strategy, f)

    print(f"✅ Created strategy YAML: {filename} (strategy: {strategy_type}, net credit after fees: {net_credit})")

def process_expirations(df):
    exp_df = df[(df['Type'] == 'Receive Deliver') & (df['Sub Type'] == 'Expiration')]
    if exp_df.empty:
        return

    print("\n🔍 Processing expirations...")
    strategies = [f for f in os.listdir(STRATEGY_DIR) if f.endswith('.yaml')]

    for fname in strategies:
        path = os.path.join(STRATEGY_DIR, fname)
        with open(path, 'r') as f:
            strategy = yaml.safe_load(f)

        modified = False
        expiration_notes = []
        expiration_date = None

        for _, row in exp_df.iterrows():
            print(f"\n🔎 Checking expiration candidate: {row['Call or Put']} {row['Strike Price']} exp {row['Expiration Date']} (Qty {row['Quantity']})")

            exp_strike = float(row['Strike Price'])
            exp_expiry = pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d')
            exp_type = row['Call or Put'].lower()
            exp_contracts = int(row['Quantity'])
            expiration_date = exp_expiry

            for leg in strategy['legs']:
                if (leg['strike'] == exp_strike and
                    leg['expiry'] == exp_expiry and
                    leg['type'] == exp_type and
                    leg['side'] == 'short' and
                    leg['contracts'] == exp_contracts and
                    'status' not in leg):

                    print(f"✅ Matched leg in {fname}: {leg}")
                    leg['status'] = 'expired'
                    expiration_notes.append(f"Expired worthless: {leg['type'].upper()} {leg['strike']} ({leg['expiry']})")
                    modified = True

        if modified:
            active_legs = [leg for leg in strategy['legs'] if 'status' not in leg or leg['status'] != 'expired']

            strategy['notes'] = strategy.get('notes', '') + "\n" + "\n".join(expiration_notes)

            if not active_legs:
                strategy['status'] = 'closed'
                strategy['closed'] = expiration_date
                strategy['notes'] += f"\nClosed via expiration on {expiration_date}"
                archive_path = os.path.join(ARCHIVE_DIR, fname)
                with open(archive_path, 'w') as f:
                    yaml.dump(strategy, f)
                os.remove(path)
                print(f"📦 Archived {fname} (all legs expired)")
            else:
                with open(path, 'w') as f:
                    yaml.dump(strategy, f)
                print(f"♻️ Updated {fname} with expiration info")


if __name__ == '__main__':
    ENABLE_MULTI_ORDER_DETECTION = True
    files = [f for f in os.listdir(TRANSACTIONS_DIR) if f.endswith(".csv")]
    for file in files:
        print(f"\n📄 Processing {file}...")
        df = load_transaction_file(os.path.join(TRANSACTIONS_DIR, file))

        process_expirations(df)

        df = df[df['Type'] == 'Trade']
        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
        df['Underlying'] = df['Symbol'].apply(lambda s: normalize_ticker(s.split()[0].replace('/', '')))
        combo_groups = df.groupby(['Date', 'Underlying'])

        for (trade_date, base_underlying), rows in combo_groups:
            print(f"\n🧾 Combined strategy candidate on {base_underlying} for {trade_date}:")
            for _, row in rows.iterrows():
                print(f" - {row['Action']}: {row['Call or Put']} {row['Strike Price']} exp {row['Expiration Date']} @ {row['Average Price']} | Fees: {row['Fees']}")

            confirm = input("Generate strategy YAML from this multi-order group? [y/N]: ").strip().lower()
            if confirm == 'y':
                order_id = int(rows['Order #'].iloc[0]) if 'Order #' in rows and not pd.isna(rows['Order #'].iloc[0]) else 0
                generate_yaml_from_order(order_id, rows.to_dict(orient='records'))

