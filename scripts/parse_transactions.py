# scripts/parse_transactions.py
import os
import pandas as pd
import yaml
from datetime import datetime
from helpers import (
    STRATEGY_DIR,
    TRANSACTIONS_DIR,
    ARCHIVE_DIR,
    load_transaction_file,
    normalize_ticker,
    detect_strategy_type,
    generate_yaml_from_order,
    update_strategy_with_roll,
    match_legs
)

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
                existing_files = [f for f in os.listdir(STRATEGY_DIR) if f.endswith(".yaml")]
                if existing_files:
                    print("📂 Open strategies:")
                    for idx, fname in enumerate(existing_files):
                        print(f" [{idx}] {fname}")
                roll_hint = any("CLOSE" in a for a in rows['Action']) and any("OPEN" in a for a in rows['Action'])
                if roll_hint:
                    print("⚠️  This order may represent a roll (close + open legs). You may want to link this to an existing strategy.")
                index = input("Link to existing strategy? Enter number or leave blank to create new: ").strip()
                if index.isdigit():
                    strategy_file = existing_files[int(index)]
                    update_strategy_with_roll(strategy_file, rows.to_dict(orient='records'), order_id, trade_date)
                else:
                    generate_yaml_from_order(order_id, rows.to_dict(orient='records'))

