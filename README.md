## Usage

### Parse daily transactions
```python scripts/parse_transactions.py```

### Track All Open Trades
```python scripts/track_trades.py track```

### Close a trade
```python track_trades.py close spy_ic_2025-03-28.yaml 145.25```

### Analyze Trades
```scripts/python track_trades.py analyze```

### Analyze performance
```scripts/python analyze_performance.py```

### How to create a virtual environment to run the scripts
```
cd ~/Documents/inversion/options-tracker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### How to deactivate the virtual environment
```
deactivate
```

### How to run the scripts in the virtual environment
```
cd ~/Documents/inversion/options-tracker
source .venv/bin/activate
python scripts/track_trades.py track
python scripts/analyze_trades.py
```
