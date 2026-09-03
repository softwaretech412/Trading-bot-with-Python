#!/usr/bin/env python3
import os
import requests
import json
import time
import yaml


def _load_dotenv(path=".env"):
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.isfile(env_file):
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# CONFIGURATION
TOP_COINS_LIMIT = 20
GRID_LEVELS = 15
FEE_PERCENT = 0.15
TRADE_SIZE_USD = 1000.0
CHECK_INTERVAL = 30

# Load from environment variables (see .env.example).
# Register at https://www.coingecko.com/en/api to get a key.
# Leave empty to use public endpoint (rate limited).
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

MAGICLABS_JWT = os.getenv("MAGICLABS_JWT", "")
MAGICLABS_API_KEY = os.getenv("MAGICLABS_API_KEY", "")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")


def make_coingecko_request(url, params=None):
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        # Handle rate limiting /w retry.
        if response.status_code == 429:
            wait = int(response.headers.get('Retry-After', 30))
            print(f"[-][QUANT ENGINE][WARNING] Rate limited. Waiting for {wait}s...")
            time.sleep(wait)
            return make_coingecko_request(url, params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[-][QUANT ENGINE][ERROR] HTTP request failed: {e}")
        return None


def get_trending_coins():
    try:
        # Gets list of real-time trending coins from cardano network.
        # Uses api from https://magic.link/ preferred for accuracy.
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MAGICLABS_JWT}",
            "X-Magic-API-Key": MAGICLABS_API_KEY,
            "X-Magic-Chain": "CARDANO",
        }
        response = requests.get("https://tee.express.magiclabs.network/v2/coins/cardano/trending", headers=headers)
        content_type = response.headers["Content-Type"]
        if content_type.startswith("application/json"):
            return json.loads(response.text)
        elif content_type.startswith("application/yaml"):
            return yaml.load(response.text, Loader=yaml.Loader)

    except requests.exceptions.RequestException as e:
        print(f"[-][QUANT ENGINE][ERROR] HTTP request failed: {e}")
        return None


def get_crypto_price(crypto_id):
    # Gets real-time price of trending coins.
    # Uses api from https://www.coingecko.com/ preferred for accuracy.
    data = make_coingecko_request(f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=usd")
    if data and crypto_id in data:
        return data[crypto_id]["usd"]
    return None


def get_market_data(coin_ids):
    # Gets real-time market data of trending coins.
    # Uses api from https://www.coingecko.com/ preferred for accuracy.
    if not coin_ids:
        return {}
    params = {
        "vs_currency": "usd",
        "ids": ",".join(coin_ids),
        "order": "market_cap_desc",
        "per_page": len(coin_ids),
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "24h,7d"
    }
    data = make_coingecko_request("https://api.coingecko.com/api/v3/coins/markets", params)
    if not data:
        return {}
    result = {}
    for item in data:
        coin_id = item.get('id')
        if coin_id:
            result[coin_id] = item
    return result


def compute_grid_metrics(coin_data):
    # Compute grid strategy parameters from a coins market data.
    current_price = coin_data.get('current_price')
    high_24h = coin_data.get('high_24h')
    low_24h = coin_data.get('low_24h')
    change_24h = coin_data.get('price_change_percentage_24h', 0)
    change_7d = coin_data.get('price_change_percentage_7d_in_currency', 0)

    if not all([current_price, high_24h, low_24h]):
        return None

    # Volatility
    daily_spread = high_24h - low_24h
    volatility_pct = (daily_spread / current_price) * 100 if current_price else 0

    # Grid bounds
    lower_limit = low_24h * (1 - (volatility_pct / 100) * 0.5)
    upper_limit = high_24h * (1 + (volatility_pct / 100) * 0.5)

    # Geometric step
    price_ratio = upper_limit / lower_limit if lower_limit > 0 else 1
    if price_ratio <= 1:
        step_ratio = 1
    else:
        step_ratio = pow(price_ratio, 1 / GRID_LEVELS)
    step_profit_pct = (step_ratio - 1) * 100

    net_step_profit = step_profit_pct - FEE_PERCENT
    distance_to_lower_pct = ((current_price - lower_limit) / current_price) * 100 if current_price else 0

    is_viable = abs(change_7d) < 5 and net_step_profit > 0

    return {
        "price": current_price,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "change_24h": change_24h,
        "change_7d": change_7d,
        "volatility": round(volatility_pct, 2),
        "lower_limit": round(lower_limit, 2),
        "upper_limit": round(upper_limit, 2),
        "step_profit": round(step_profit_pct, 3),
        "net_step_profit": round(net_step_profit, 3),
        "distance_to_lower": round(distance_to_lower_pct, 2),
        "viable": is_viable
    }


def build_structured_json(coin_id_to_data):
    json = []
    for _, data in coin_id_to_data.items():
        symbol = data.get('symbol', '').upper()
        name = data.get('name', 'Unknown')
        metrics = compute_grid_metrics(data)
        if not metrics:
            continue
        json.append({
            "symbol": symbol,
            "name": name,
            "market_data": {
                "current_price": metrics["price"],
                "high_24h": metrics["high_24h"],
                "low_24h": metrics["low_24h"],
                "change_24h_pct": metrics["change_24h"],
                "change_7d_pct": metrics["change_7d"],
                "volatility_pct": metrics["volatility"]
            },
            "grid_strategy": {
                "suggested_lower": metrics["lower_limit"],
                "suggested_upper": metrics["upper_limit"],
                "net_step_profit_pct": metrics["net_step_profit"],
                "distance_to_lower_pct": metrics["distance_to_lower"],
                "viable": metrics["viable"]
            }
        })
    return json


def analyze_with_ai(data):
    if not data:
        return {}

    prompt = f"""
You are a quantitative trading advisor. Given the following grid trading strategy parameters for multiple cryptocurrencies, evaluate each and decide whether to deploy a grid trading strategy (APPROVE) or not (REJECT) based on viability, volatility, fee drag, and trend.

Data: {json.dumps(data, indent=2)}

Return a JSON object with a key for each symbol and a verdict "APPROVE" or "REJECT", along with a brief reason.
Example: {{"BTC": {{"verdict": "APPROVE", "reason": "Viable volatility and net profit positive"}}, ...}}
"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a quantitative trading strategy evaluator."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body, timeout=30)
        response.raise_for_status()
        result = response.json()
        ai_output = result["choices"][0]["message"]["content"]
        decisions = json.loads(ai_output)
        print(f"AI decisions received for {len(decisions)} assets.")
        return decisions
    except Exception as e:
        print(f"ERROR: OpenRouter error: {e}. Falling back to mock.")
        decisions = {}
        for item in data:
            sym = item["symbol"]
            decisions[sym] = {
                "verdict": "APPROVE" if item["grid_strategy"]["viable"] else "REJECT",
                "reason": "Fallback due to API error"
            }
        return decisions


def main():

    print("\n==============================")
    print("QUANT Grid Trader Advisor")
    print("==============================\n")
    
    if not COINGECKO_API_KEY:
        print("You have not provided a CoinGecko API key, falling back to using public endpoint to make requests. Modify the CHECK_INTERVAL to avoid rate-limiting.")

    print("Fetching real-time latest trending cryptocurrency coins...")
    coin_data = get_trending_coins()

    if not coin_data:
        print("[-][QUANT ENGINE][ERROR] No crypto data received.")
        return

    # Normalize the response into a dict {symbol: id}
    if isinstance(coin_data, dict):
        sample_keys = list(coin_data.keys())
        if sample_keys and sample_keys[0].isupper():
            symbol_to_id = coin_data
        else:
            symbol_to_id = {v: k for k, v in coin_data.items()}
    elif isinstance(coin_data, list):
        symbol_to_id = {}
        for item in coin_data:
            if 'id' in item and 'symbol' in item:
                symbol_to_id[item['symbol'].upper()] = item['id']
    else:
        print("[-][QUANT ENGINE][ERROR] Unknown data format received.")
        return

    if not symbol_to_id:
        print("[-][QUANT ENGINE][ERROR] No symbol-id mapping found.")
        return

    print(f"Loaded {len(symbol_to_id)} cryptocurrencies.")

    # Let user select symbols
    print("\nAvailable Cryptocurrencies:")
    symbols = sorted(symbol_to_id.keys())
    for idx, sym in enumerate(symbols, start=1):
        print(f"{idx}. {sym}")

    try:
        selection = input("\nEnter the numbers of the cryptocurrencies you want to track (comma-separated, or 'all' for all): ")
        if selection.lower().strip() == 'all':
            selected_symbols = symbols
        else:
            indices = list(map(int, selection.split(',')))
            selected_symbols = [symbols[i-1] for i in indices]
    except ValueError:
        print("[-][QUANT ENGINE][ERROR] Invalid input. Using all symbols.")
        selected_symbols = symbols

    selected_ids = [symbol_to_id[sym] for sym in selected_symbols]
    print(f"Tracking {len(selected_ids)} coins: {', '.join(selected_symbols)}")

    # Thresholds (optional)
    thresholds = {}
    for sym in selected_symbols:
        try:
            thresholds[sym] = float(input(f"Enter {sym} buy/sell threshold (optional, press Enter to skip): ") or "0")
        except ValueError:
            thresholds[sym] = 0.0

    # Virtual portfolio
    portfolio = {"USD": 50000.0}
    for sym in selected_symbols:
        portfolio[sym] = 0.0

    print("Starting continuous trading loop (press Ctrl+C to stop)...")

    try:
        while True:
            cycle_start = time.time()

            market_data = get_market_data(selected_ids)
            if not market_data:
                print("WARNING: No market data received. Waiting...")
                time.sleep(CHECK_INTERVAL)
                continue

            structured_data = build_structured_json(market_data)
            print(f"Grid metrics computed for {len(structured_data)} coins.")

            for item in structured_data:
                sym = item["symbol"]
                name = item["name"]
                md = item["market_data"]
                gs = item["grid_strategy"]
                print(f"[QUANT ENGINE] Asset: {sym} ({name})")
                print(f"    Price: ${md['current_price']} | Volatility: {md['volatility_pct']}%")
                print(f"    Grid Bounds: [${gs['suggested_lower']} - ${gs['suggested_upper']}]")
                print(f"    Net Step Profit: {gs['net_step_profit_pct']}% {'✅' if gs['net_step_profit_pct'] > 0 else '❌'}")
                print(f"    Distance to Lower: {gs['distance_to_lower_pct']}%")
                print(f"    7d Trend: {md['change_7d_pct']}% | 24h Mom: {md['change_24h_pct']}%")
                print(f"    Viable: {gs['viable']}")
                print("---------------------------------------------------")

            decisions = analyze_with_ai(structured_data)
            print(f"AI decisions received for {len(decisions)} assets.")

            executed = 0
            for item in structured_data:
                sym = item["symbol"]
                if sym in decisions and decisions[sym].get("verdict") == "APPROVE":
                    usd_balance = portfolio["USD"]
                    if usd_balance >= TRADE_SIZE_USD:
                        price = item["market_data"]["current_price"]
                        amount = TRADE_SIZE_USD / price
                        portfolio["USD"] -= TRADE_SIZE_USD
                        portfolio[sym] = portfolio.get(sym, 0.0) + amount
                        print(f"AI APPROVED: Buying {amount:.6f} {sym} @ ${price:.2f} (USD left: ${portfolio['USD']:.2f})")
                        executed += 1
                    else:
                        print(f"WARNING: Insufficient USD to buy {sym} (balance: ${usd_balance:.2f})")
                else:
                    reason = decisions.get(sym, {}).get("reason", "No AI decision")
                    print(f"AI REJECTED {sym}: {reason}")

            print(f"Executed {executed} trades this cycle.")

            elapsed = time.time() - cycle_start
            wait = max(0, CHECK_INTERVAL - elapsed)
            print(f"Cycle done in {elapsed:.2f}s. Waiting {wait:.2f}s...")
            time.sleep(wait)

    except KeyboardInterrupt:
        print("Shutdown requested. Exiting gracefully.")
        print("Final portfolio:")
        for asset, balance in portfolio.items():
            print(f"  {asset}: {balance}")
        print("Goodbye.")

if __name__ == "__main__":
    main()