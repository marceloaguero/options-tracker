# scripts/analyze_trades.py
import os
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

# Resolve paths relative to the script location
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(BASE_DIR, "charts")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def plot_metrics(log_file):
    df = pd.read_csv(os.path.join(LOG_DIR, log_file))
    df['Date'] = pd.to_datetime(df['Date'])
    base_name = os.path.splitext(log_file)[0]

    metrics = ['PnL', '% of Max Profit', 'Beta Delta', 'IV Rank', 'PoP', 'Theta']

    for metric in metrics:
        if metric in df.columns:
            plt.figure(figsize=(10, 4))
            plt.plot(df['Date'], df[metric], marker='o')
            plt.title(f"{metric} over Time - {base_name}")
            plt.xlabel("Date")
            plt.ylabel(metric)
            plt.grid(True)
            plt.tight_layout()
            plt.savefig(os.path.join(OUTPUT_DIR, f"{base_name}_{metric.replace(' ', '_')}.png"))
            plt.close()
            print(f"Saved chart: {base_name}_{metric.replace(' ', '_')}.png")

if __name__ == '__main__':
    for file in os.listdir(LOG_DIR):
        if file.endswith(".csv"):
            plot_metrics(file)

