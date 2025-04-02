# scripts/track_trades.py
import os
import yaml
import pandas as pd
import argparse
from datetime import datetime

# Resolve paths relative to the script location
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STRATEGY_DIR = os.path.join(BASE_DIR, "strategies")
LOG_DIR = os.path.join(BASE_DIR, "logs")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
CSV_PATH = os.path.join(BASE_DIR, "tastytrade_positions.csv")
CLOSED_SUMMARY = os.path.join(BASE_DIR, "closed_trades.csv")

def load_tastytrade_csv():
    df = pd.read_csv(CSV_PATH)
    df.columns = df.columns.str.strip()  # Clean column names
    return df

def load_strategy_yamls(status_filter=None):
    strategies = []
    for file in os.listdir(STRATEGY_DIR):
        if file.endswith(".yaml"):
            with open(os.path.join(STRATEGY_DIR, file), 'r') as f:
                data = yaml.safe_load(f)
                data['file'] = file
                if status_filter is None or data.get('status') == status_filter:
                    strategies.append(data)
    return strategies

def match_legs(strategy, positions_df):
    legs = strategy['legs']
    matched = []
    for leg in legs:
        strike = leg['strike']
        expiry = leg['expiry']
        callput = leg['type'].capitalize()

        match = positions_df[
            (positions_df['Strike Price'] == strike) &
            (positions_df['Call/Put'] == callput) &
            (positions_df['Exp Date'].str.contains(expiry[-2:]))
        ]
        if not match.empty:
            matched.append(match.iloc[0])
    return matched

def append_daily_log(strategy, matched_legs):
    log_file = os.path.join(LOG_DIR, strategy['file'].replace('.yaml', '.csv'))
    today = datetime.today().strftime("%Y-%m-%d")

    delta = sum([float(leg['Delta']) for leg in matched_legs])
    beta_delta = sum([float(leg.get('β Delta', 0)) for leg in matched_legs])
    theta = sum([float(leg['Theta']) for leg in matched_legs])
    iv_rank = float(matched_legs[0]['IV Rank'])
    pop = matched_legs[0].get('PoP', '').replace('%', '')
    try:
        pop = float(pop)
    except:
        pop = None

    underlying = matched_legs[0]['Underlying'].replace(',', '')
    try:
        underlying = float(underlying)
    except:
        underlying = None

    pnl = sum([float(leg['Ext']) for leg in matched_legs])

    # Calculate % of max profit if defined
    initial_credit = strategy.get('initial_credit', None)
    max_profit_pct = round(pnl / initial_credit * 100, 2) if initial_credit else None

    row = {
        'Date': today,
        'Underlying Price': underlying,
        'Delta': delta,
        'Beta Delta': beta_delta,
        'Theta': theta,
        'IV Rank': iv_rank,
        'PoP': pop,
        'PnL': pnl,
        '% of Max Profit': max_profit_pct
    }

    if os.path.exists(log_file):
        df = pd.read_csv(log_file)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])

    df.to_csv(log_file, index=False)
    print(f"✅ Logged data for {strategy['ticker']} ({strategy['strategy']})")

def close_trade(strategy_file, pnl):
    strategy_path = os.path.join(STRATEGY_DIR, strategy_file)
    with open(strategy_path, 'r') as f:
        data = yaml.safe_load(f)

    data['status'] = 'closed'
    data['closed'] = datetime.today().strftime('%Y-%m-%d')
    data['realized_pnl'] = pnl

    # Archive the YAML
    archive_path = os.path.join(ARCHIVE_DIR, strategy_file)
    with open(archive_path, 'w') as f:
        yaml.dump(data, f)

    # Move log file
    log_file = strategy_file.replace('.yaml', '.csv')
    os.rename(os.path.join(LOG_DIR, log_file), os.path.join(ARCHIVE_DIR, log_file))

    # Remove from active strategies
    os.remove(strategy_path)

    # Append to closed summary
    row = {
        'strategy': data['strategy'],
        'ticker': data['ticker'],
        'opened': data['opened'],
        'closed': data['closed'],
        'pnl': data['realized_pnl'],
        'tags': ','.join(data.get('tags', []))
    }
    if os.path.exists(CLOSED_SUMMARY):
        df = pd.read_csv(CLOSED_SUMMARY)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.to_csv(CLOSED_SUMMARY, index=False)

    print(f"✅ Closed trade: {data['ticker']} ({data['strategy']})")

def main():
    parser = argparse.ArgumentParser(description="Options Tracker CLI")
    subparsers = parser.add_subparsers(dest="command")

    track_parser = subparsers.add_parser("track", help="Track all open trades")

    close_parser = subparsers.add_parser("close", help="Close a trade")
    close_parser.add_argument("strategy_file", help="YAML filename of the strategy to close")
    close_parser.add_argument("pnl", type=float, help="Realized PnL of the trade")

    args = parser.parse_args()

    if args.command == "track":
        tasty_df = load_tastytrade_csv()
        strategies = load_strategy_yamls(status_filter='open')
        for strat in strategies:
            matched = match_legs(strat, tasty_df)
            if matched:
                append_daily_log(strat, matched)
            else:
                print(f"⚠️ No match for strategy: {strat['file']}")

    elif args.command == "close":
        close_trade(args.strategy_file, args.pnl)

if __name__ == '__main__':
    main()

