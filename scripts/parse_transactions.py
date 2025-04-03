# scripts/parse_transactions.py
import os
import pandas as pd
import yaml
from datetime import datetime
from collections import defaultdict
import shutil
import re

# Constants
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRANSACTIONS_DIR = os.path.join(BASE_DIR, "transactions")
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

# Utility Functions
def normalize_ticker(ticker):
    return re.sub(r'[^A-Z]', '', ticker.upper())

def load_transaction_file(filepath):
    df = pd.read_csv(filepath)
    df = df[df['Type'].isin(['Trade', 'Receive Deliver'])]  # Limit to relevant transactions
    return df

def detect_strategy_type(legs):
    puts = [leg for leg in legs if leg['type'] == 'put']
    calls = [leg for leg in legs if leg['type'] == 'call']
    shorts = [leg for leg in legs if leg['side'] == 'short']
    longs = [leg for leg in legs if leg['side'] == 'long']

    if len(puts) == 4 and len(shorts) == 3 and len(longs) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_put = next((leg for leg in longs if leg['type'] == 'put'), None)
        if long_put:
            long_expiry = long_put['expiry']
            debit_spread_short = next((leg for leg in short_puts if leg['expiry'] == long_expiry), None)
            near_term_puts = [leg for leg in short_puts if leg['expiry'] != long_expiry]
            if debit_spread_short and len(near_term_puts) == 2:
                return "Calendar 1-1-2"

    if len(legs) == 4:
        put_legs = [l for l in legs if l['type'] == 'put']
        if len(put_legs) == 4:
            strikes = sorted([l['strike'] for l in put_legs])
            expiries = list(set(l['expiry'] for l in put_legs))
            if len(expiries) == 1:
                return "Broken Wing Put Condor"

    if len(legs) == 2 and all(l['type'] == 'put' for l in legs):
        return "Put Vertical"

    if len(legs) == 1 and shorts:
        return "Short Put" if puts else "Short Call"

    return "Unnamed"

def calculate_initial_credit(legs):
    return round(sum(leg['entry_price'] * (1 if leg['side'] == 'short' else -1) for leg in legs), 2)

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

def is_roll_candidate(df):
    return any("CLOSE" in x for x in df['Action'].values) and any("OPEN" in x for x in df['Action'].values)

def generate_yaml_from_order(order_id, rows, trade_date):
    legs = []
    gross_credit = 0.0
    total_fees = 0.0
    for row in rows:
        entry_price = row['Average Price']
        if isinstance(entry_price, str):
            entry_price = float(entry_price.replace(',', ''))
        symbol = row['Underlying Symbol'] if 'Underlying Symbol' in row else row['Root Symbol']
        leg = {
            'type': row['Call or Put'].lower(),
            'side': 'short' if row['Action'].endswith('TO_OPEN') and float(row['Quantity']) > 0 else 'long',
            'strike': float(row['Strike Price']),
            'expiry': pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d'),
            'entry_price': abs(entry_price / 100),
            'contracts': int(row['Quantity']),
            'ticker': symbol
        }
        legs.append(leg)
        gross_credit += float(row['Value']) if isinstance(row['Value'], (float, int)) else float(row['Value'].replace(',', ''))
        total_fees += float(row['Fees'])

    strategy = {
        'ticker': normalize_ticker(rows[0]['Underlying Symbol'] if 'Underlying Symbol' in rows[0] else rows[0]['Root Symbol']),
        'opened': trade_date,
        'order_ids': [order_id],
        'legs': legs,
        'initial_credit': round(gross_credit - total_fees, 2),
        'strategy': detect_strategy_type(legs),
        'status': 'open',
        'tags': [],
        'roll_count': 0,
        'notes': ''
    }

    filename = f"{strategy['ticker'].lower()}_{trade_date}.yaml"
    with open(os.path.join(STRATEGY_DIR, filename), 'w') as f:
        yaml.dump(strategy, f)

    return filename

def update_strategy_with_roll(file_path, new_rows, order_id, trade_date):
    path = os.path.join(STRATEGY_DIR, file_path)
    with open(path, 'r') as f:
        strategy = yaml.safe_load(f)

    for row in new_rows:
        entry_price = row['Average Price']
        if isinstance(entry_price, str):
            entry_price = float(entry_price.replace(',', ''))
        leg = {
            'type': row['Call or Put'].lower(),
            'side': 'short' if row['Action'] == 'SELL_TO_OPEN' else 'long' if row['Action'] == 'BUY_TO_OPEN' else '',
            'strike': float(row['Strike Price']),
            'expiry': pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d'),
            'entry_price': abs(entry_price / 100),
            'contracts': int(row['Quantity']),
            'ticker': row['Underlying Symbol'] if 'Underlying Symbol' in row else row['Root Symbol']
        }

        if row['Action'] in ['BUY_TO_CLOSE', 'SELL_TO_CLOSE']:
            for existing_leg in strategy['legs']:
                if all([
                    existing_leg['strike'] == leg['strike'],
                    existing_leg['expiry'] == leg['expiry'],
                    existing_leg['side'] != leg['side'],
                    existing_leg['type'] == leg['type'],
                    existing_leg['ticker'] == leg['ticker'],
                    'status' not in existing_leg
                ]):
                    existing_leg['status'] = 'closed'
                    break
        else:
            strategy['legs'].append(leg)

    strategy['order_ids'].append(order_id)
    strategy['roll_count'] += 1
    strategy['tags'] = list(set(strategy.get('tags', []) + ['rolled']))
    note = f"\n\n  Rolled on {trade_date} (order #{order_id})"
    strategy['notes'] = strategy.get('notes', '') + note
    strategy['initial_credit'] = calculate_initial_credit(strategy['legs'])

    with open(path, 'w') as f:
        yaml.dump(strategy, f)

    print(f"🔁 Updated existing strategy: {file_path}")
    return file_path

# Main script
if __name__ == '__main__':
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

            strategy_files = [f for f in os.listdir(STRATEGY_DIR) if f.endswith('.yaml')]

            confirm = input("Generate strategy YAML from this multi-order group? [y/N]: ").strip().lower()
            if confirm == 'y':
                order_id = int(rows['Order #'].iloc[0]) if 'Order #' in rows and not pd.isna(rows['Order #'].iloc[0]) else 0

                rolled_file = None
                if is_roll_candidate(rows):
                    print("⚠️  This order may represent a roll (close + open legs). You may want to link this to an existing strategy.")
                    print("📂 Open strategies:")
                    for i, file in enumerate(strategy_files):
                        print(f" [{i}] {file}")
                    strategy_choice = input("Link to existing strategy? Enter number or leave blank to create new: ").strip()
                    if strategy_choice.isdigit():
                        strategy_file = strategy_files[int(strategy_choice)]
                        rolled_file = update_strategy_with_roll(strategy_file, rows.to_dict(orient='records'), order_id, trade_date)

                if not rolled_file:
                    generated_file = generate_yaml_from_order(order_id, rows.to_dict(orient='records'), trade_date)
                    print(f"✅ Created strategy YAML: {generated_file}")

