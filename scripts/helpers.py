# scripts/helpers.py
import os
import pandas as pd
import yaml
from datetime import datetime
from collections import defaultdict
import re

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRANSACTIONS_DIR = os.path.join(BASE_DIR, "transactions")
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

def normalize_ticker(ticker):
    return re.sub(r"[^A-Z]", "", ticker.upper())

def load_transaction_file(filepath):
    df = pd.read_csv(filepath)
    df = df[df['Action'].notna() & df['Symbol'].notna()]
    return df

def detect_strategy_type(legs):
    puts = [leg for leg in legs if leg['type'] == 'put']
    calls = [leg for leg in legs if leg['type'] == 'call']
    shorts = [leg for leg in legs if leg['side'] == 'short']
    longs = [leg for leg in legs if leg['side'] == 'long']
    
    expiries = list(set(leg['expiry'] for leg in legs))
    base_tickers = list(set(normalize_ticker(leg['ticker']) for leg in legs))

    if len(puts) == 3 and len(shorts) == 2 and len(longs) == 1 and len(base_tickers) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_puts = [leg for leg in longs if leg['type'] == 'put']
        if len(short_puts) == 2 and len(long_puts) == 1:
            short_expiry = short_puts[0]['expiry']
            if all(leg['expiry'] == short_expiry for leg in short_puts):
                return "Calendar 1-1-2"

    if len(puts) == 4 and len(shorts) == 2 and len(longs) == 2 and len(expiries) == 1:
        strikes = sorted(set([leg['strike'] for leg in puts]))
        if len(strikes) == 4:
            return "Broken Wing Put Condor"

    if len(puts) == 2 and len(shorts) == 1 and len(longs) == 1 and expiries.count(expiries[0]) == 2:
        return "Put Vertical"

    if len(puts) == 1 and len(shorts) == 1:
        return "Short Put"

    return "Unnamed"

def calculate_initial_credit(legs):
    credit = 0.0
    for leg in legs:
        sign = 1 if leg['side'] == 'short' else -1
        credit += sign * leg['entry_price'] * leg['contracts']
    return round(credit, 2)

def generate_yaml_from_order(order_id, orders, trade_date):
    legs = []
    for row in orders:
        action = row['Action']
        if 'PUT' not in row['Call or Put'] and 'CALL' not in row['Call or Put']:
            continue

        side = 'short' if 'SELL' in action else 'long'
        contracts = int(row['Quantity'])
        entry_price = abs(float(str(row['Average Price']).replace(',', '')) / 100)

        leg = {
            'contracts': contracts,
            'entry_price': entry_price,
            'expiry': pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d'),
            'side': side,
            'strike': float(row['Strike Price']),
            'ticker': normalize_ticker(row['Root Symbol']),
            'type': row['Call or Put'].lower(),
        }
        legs.append(leg)

    base_ticker = normalize_ticker(orders[0]['Underlying Symbol'])
    fname = f"{base_ticker.lower()}_{trade_date}.yaml"
    filepath = os.path.join(STRATEGY_DIR, fname)

    strategy = {
        'initial_credit': calculate_initial_credit(legs),
        'legs': legs,
        'notes': '',
        'opened': trade_date,
        'order_ids': [order_id],
        'roll_count': 0,
        'status': 'open',
        'strategy': detect_strategy_type(legs),
        'tags': [],
        'ticker': base_ticker
    }
    with open(filepath, 'w') as f:
        yaml.dump(strategy, f)

    print(f"✅ Created strategy YAML: {fname} (strategy: {strategy['strategy']}, net credit after fees: {strategy['initial_credit']})")

def match_legs(leg1, leg2):
    return (
        leg1['type'] == leg2['type'] and
        leg1['strike'] == leg2['strike'] and
        leg1['expiry'] == leg2['expiry'] and
        leg1['side'] == leg2['side'] and
        leg1['ticker'] == leg2['ticker']
    )

def update_strategy_with_roll(file, orders, order_id, trade_date):
    path = os.path.join(STRATEGY_DIR, file)
    with open(path, 'r') as f:
        strategy = yaml.safe_load(f)

    close_legs = []
    open_legs = []

    for row in orders:
        action = row['Action']
        if 'PUT' not in row['Call or Put'] and 'CALL' not in row['Call or Put']:
            continue
        side = 'short' if 'SELL' in action else 'long'
        contracts = int(row['Quantity'])
        entry_price = abs(float(str(row['Average Price']).replace(',', '')) / 100)

        leg = {
            'contracts': contracts,
            'entry_price': entry_price,
            'expiry': pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d'),
            'side': side,
            'strike': float(row['Strike Price']),
            'ticker': normalize_ticker(row['Root Symbol']),
            'type': row['Call or Put'].lower(),
        }
        if 'TO_CLOSE' in action:
            close_legs.append(leg)
        else:
            open_legs.append(leg)

    for c_leg in close_legs:
        for s_leg in strategy['legs']:
            if match_legs(c_leg, s_leg) and 'status' not in s_leg:
                s_leg['status'] = 'closed'
                break

    strategy['legs'].extend(open_legs)
    strategy['initial_credit'] = calculate_initial_credit(strategy['legs'])
    strategy['order_ids'].append(order_id)
    strategy['roll_count'] += 1
    strategy['notes'] += f"\n\n  Rolled on {trade_date} (order #{order_id})"

    strategy['notes'] = re.sub(r'\n{3,}', '\n\n', strategy['notes']).strip()
    strategy['tags'] = list(set(strategy['tags'] + ['rolled']))

    deduped = []
    seen = set()
    for leg in strategy['legs']:
        key = (leg['type'], leg['strike'], leg['expiry'], leg['side'], leg['ticker'], leg.get('status'))
        if key not in seen:
            seen.add(key)
            deduped.append(leg)
    strategy['legs'] = deduped

    with open(path, 'w') as f:
        yaml.dump(strategy, f)

    print(f"🔁 Updated existing strategy: {file}")
    return file  # Make sure to return the updated file name


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
            exp_strike = float(row['Strike Price'])
            exp_expiry = pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d')
            exp_type = row['Call or Put'].lower()
            exp_contracts = int(row['Quantity'])
            expiration_date = exp_expiry

            for leg in strategy['legs']:
                if (
                    leg['strike'] == exp_strike and
                    leg['expiry'] == exp_expiry and
                    leg['type'] == exp_type and
                    leg['side'] == 'short' and
                    leg['contracts'] == exp_contracts and
                    'status' not in leg
                ):
                    leg['status'] = 'expired'
                    expiration_notes.append(f"Expired worthless: {leg['type'].upper()} {leg['strike']} ({leg['expiry']})")
                    modified = True

        if modified:
            active_legs = [leg for leg in strategy['legs'] if leg.get('status') not in ['expired']]
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

