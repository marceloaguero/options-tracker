# scripts/helpers.py
import os
import pandas as pd
import yaml
from datetime import datetime
from difflib import SequenceMatcher

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TRANSACTIONS_DIR = os.path.join(BASE_DIR, "transactions")
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")


def normalize_ticker(ticker):
    return ticker.replace('/', '').rstrip('0123456789').upper()


def load_transaction_file(path):
    df = pd.read_csv(path)
    df = df[df['Action'].notna()]  # Ignore headers or empty rows
    df['Date'] = pd.to_datetime(df['Date'])
    return df


def match_legs(leg_a, leg_b):
    return (
        leg_a['type'] == leg_b['type'] and
        leg_a['side'] == leg_b['side'] and
        leg_a['strike'] == leg_b['strike'] and
        leg_a['expiry'] == leg_b['expiry'] and
        normalize_ticker(leg_a['ticker']) == normalize_ticker(leg_b['ticker'])
    )


def detect_strategy_type(legs):
    puts = [leg for leg in legs if leg['type'] == 'put']
    calls = [leg for leg in legs if leg['type'] == 'call']
    shorts = [leg for leg in legs if leg['side'] == 'short']
    longs = [leg for leg in legs if leg['side'] == 'long']

    expiries = list(set(leg['expiry'] for leg in legs))
    base_tickers = list(set(normalize_ticker(leg['ticker']) for leg in legs))

    # Calendar 1-1-2 (long-dated debit put spread + 2 near-term short puts)
    if len(puts) == 3 and len(shorts) == 2 and len(longs) == 1:
        short_puts = [leg for leg in shorts if leg['type'] == 'put']
        long_put = next((leg for leg in longs if leg['type'] == 'put'), None)
        if long_put:
            long_expiry = long_put['expiry']
            debit_spread_short = next((leg for leg in short_puts if leg['expiry'] == long_expiry), None)
            near_term_puts = [leg for leg in short_puts if leg['expiry'] != long_expiry]
            if debit_spread_short and len(near_term_puts) == 1:
                return "Calendar 1-1-2"

    if len(legs) == 4 and all(leg['type'] == 'put' for leg in legs):
        longs = sorted([leg for leg in legs if leg['side'] == 'long'], key=lambda x: x['strike'])
        shorts = sorted([leg for leg in legs if leg['side'] == 'short'], key=lambda x: x['strike'])
        if len(longs) == 2 and len(shorts) == 2:
            width_1 = shorts[1]['strike'] - shorts[0]['strike']
            width_2 = longs[1]['strike'] - longs[0]['strike']
            if abs(width_1 - width_2) < 0.01:
                return "Put Condor"
            else:
                return "Broken Wing Put Condor"

    if len(legs) == 2:
        if puts and len(shorts) == 1:
            return "Short Put"
        if calls and len(shorts) == 1:
            return "Short Call"

    return "Unnamed"


def generate_yaml_from_order(order_id, rows):
    legs = []
    gross_credit = 0
    total_fees = 0
    for row in rows:
        contracts = int(row['Quantity'])
        price_str = str(row['Average Price']).replace(',', '')
        entry_price = abs(float(price_str) / 100)
        fees = abs(float(str(row['Fees']).replace(',', '')))

        if row['Action'].endswith("TO_OPEN"):
            leg = {
                'contracts': contracts,
                'entry_price': entry_price,
                'expiry': pd.to_datetime(row['Expiration Date']).strftime('%Y-%m-%d'),
                'side': 'short' if row['Action'].startswith("SELL") else 'long',
                'strike': float(row['Strike Price']),
                'ticker': row['Root Symbol'].strip().upper(),
                'type': row['Call or Put'].lower()
            }
            legs.append(leg)
            gross_credit += float(row['Value'])
            total_fees += fees

    strategy = detect_strategy_type(legs)
    base_ticker = normalize_ticker(legs[0]['ticker']) if legs else 'unknown'
    date_str = pd.to_datetime(rows[0]['Date']).strftime('%Y-%m-%d')
    filename = f"{base_ticker.lower()}_{date_str}.yaml"

    data = {
        'initial_credit': round(gross_credit - total_fees, 2),
        'legs': legs,
        'notes': '',
        'opened': date_str,
        'order_ids': [order_id] if order_id else [],
        'roll_count': 0,
        'status': 'open',
        'strategy': strategy,
        'tags': [],
        'ticker': base_ticker
    }

    with open(os.path.join(STRATEGY_DIR, filename), 'w') as f:
        yaml.dump(data, f)

    print(f"✅ Created strategy YAML: {filename} (strategy: {strategy}, net credit after fees: {data['initial_credit']})")
    return filename


def update_strategy_with_roll(file_path, new_legs, order_id, trade_date):
    with open(os.path.join(STRATEGY_DIR, file_path), 'r') as f:

        strategy = yaml.safe_load(f)

    for leg in new_legs:
        if leg['Action'].endswith("TO_CLOSE"):
            for existing in strategy['legs']:
                if (existing['strike'] == float(leg['Strike Price']) and
                    existing['expiry'] == pd.to_datetime(leg['Expiration Date']).strftime('%Y-%m-%d') and
                    existing['type'] == leg['Call or Put'].lower() and
                    existing['side'] == ('short' if leg['Action'].startswith('BUY') else 'long')):
                    existing['status'] = 'closed'

        elif leg['Action'].endswith("TO_OPEN"):
            strategy['legs'].append({
                'contracts': int(leg['Quantity']),
                'entry_price': abs(float(str(leg['Average Price']).replace(',', '')) / 100),
                'expiry': pd.to_datetime(leg['Expiration Date']).strftime('%Y-%m-%d'),
                'side': 'short' if leg['Action'].startswith("SELL") else 'long',
                'strike': float(leg['Strike Price']),
                'ticker': leg['Root Symbol'].strip().upper(),
                'type': leg['Call or Put'].lower()
            })

    strategy['notes'] = strategy.get('notes', '') + f"\n\nRolled on {trade_date} (order #{order_id})"
    strategy['roll_count'] = strategy.get('roll_count', 0) + 1
    strategy['order_ids'].append(order_id)
    strategy.setdefault('tags', []).append('rolled')

    with open(os.path.join(STRATEGY_DIR, file_path), 'w') as f:

        yaml.dump(strategy, f)

    print(f"🔁 Updated existing strategy: {os.path.basename(file_path)}")

