#!/usr/bin/env python3
import os
import sys
import re
import signal
import logging
import random
import requests
import json
import time
import yaml
from collections import defaultdict, deque

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("quant_engine")
shutdown_requested = False


class RuntimeMonitor:
    def __init__(self, window_size, report_every_cycles):
        self.latencies = defaultdict(lambda: deque(maxlen=window_size))
        self.counters = defaultdict(int)
        self.cycle_count = 0
        self.report_every_cycles = max(1, report_every_cycles)

    def record_latency(self, service_name, latency_ms):
        self.latencies[service_name].append(float(latency_ms))

    def increment(self, counter_name, service_name=None):
        self.counters[counter_name] += 1
        if service_name:
            self.counters[f"{counter_name}.{service_name}"] += 1

    def _percentile(self, values, percentile):
        if not values:
            return 0.0
        ordered = sorted(values)
        index = int((len(ordered) - 1) * percentile)
        return ordered[index]

    def log_latency_slo(self, service_name, target_ms):
        history = list(self.latencies.get(service_name, []))
        if not history:
            logger.warning("SLO %s has no samples yet.", service_name)
            return
        p50 = self._percentile(history, 0.50)
        p95 = self._percentile(history, 0.95)
        latest = history[-1]
        status = "OK" if p95 <= target_ms else "SLOW"
        logger.info(
            "SLO %s latest=%.0fms p50=%.0fms p95=%.0fms target=%.0fms [%s]",
            service_name,
            latest,
            p50,
            p95,
            target_ms,
            status,
        )

    def log_cycle_summary(self):
        self.cycle_count += 1
        if self.cycle_count % self.report_every_cycles != 0:
            return
        logger.info(
            "Reliability summary (since startup, report interval %s cycles): retries=%s rate_limits=%s "
            "network_errors=%s server_errors=%s circuit_opens=%s circuit_skips=%s ai_fallbacks=%s",
            self.report_every_cycles,
            self.counters.get("retries", 0),
            self.counters.get("rate_limits", 0),
            self.counters.get("network_errors", 0),
            self.counters.get("server_errors", 0),
            self.counters.get("circuit_opens", 0),
            self.counters.get("circuit_skips", 0),
            self.counters.get("ai_fallbacks", 0),
        )
        for service_name, target_ms in SLO_TARGETS_MS.items():
            self.log_latency_slo(service_name, target_ms)


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s][QUANT ENGINE][%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _handle_shutdown(signum, _frame):
    global shutdown_requested
    shutdown_requested = True
    logger.info("Shutdown signal received (%s). Finishing gracefully...", signum)


def _register_shutdown_handlers():
    signal.signal(signal.SIGINT, _handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown)


def _get_system_metrics():
    if psutil is None:
        return {}
    proc = psutil.Process()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
    }


def _timed_call(service_name, func, *args, **kwargs):
    start = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info("API %s completed in %.0fms", service_name, latency_ms)
        MONITOR.record_latency(service_name, latency_ms)
        target_ms = SLO_TARGETS_MS.get(service_name)
        if target_ms and latency_ms > target_ms:
            logger.warning(
                "SLO breach for %s: %.0fms (target %.0fms)",
                service_name, latency_ms, target_ms
            )
        return result, latency_ms
    except Exception:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.exception("API %s failed after %.0fms", service_name, latency_ms)
        MONITOR.increment("timed_call_exceptions", service_name)
        raise


def _get_circuit_state(service_name):
    state = CIRCUIT_BREAKERS.get(service_name)
    if state is None:
        state = {"consecutive_failures": 0, "opened_until": 0.0}
        CIRCUIT_BREAKERS[service_name] = state
    return state


def _register_service_failure(service_name):
    state = _get_circuit_state(service_name)
    state["consecutive_failures"] += 1
    if state["consecutive_failures"] >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        state["opened_until"] = time.time() + CIRCUIT_BREAKER_COOLDOWN_SECONDS
        state["consecutive_failures"] = 0
        MONITOR.increment("circuit_opens", service_name)
        logger.error(
            "%s circuit opened for %.1fs after repeated failures.",
            service_name,
            CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )


def _register_service_success(service_name):
    state = _get_circuit_state(service_name)
    state["consecutive_failures"] = 0


def _compute_backoff_seconds(attempt, retry_after=None):
    if retry_after is not None:
        try:
            base_wait = max(0.0, float(retry_after))
        except (TypeError, ValueError):
            base_wait = API_RETRY_BACKOFF_BASE ** attempt
    else:
        base_wait = API_RETRY_BACKOFF_BASE ** attempt
    jitter = random.uniform(0, RETRY_JITTER_SECONDS) if RETRY_JITTER_SECONDS > 0 else 0.0
    return min(MAX_BACKOFF_SECONDS, base_wait + jitter)


def _http_request_with_retry(method, url, service_name, attempt=0, **kwargs):
    state = _get_circuit_state(service_name)
    now = time.time()
    if state["opened_until"] > now:
        remaining = state["opened_until"] - now
        MONITOR.increment("circuit_skips", service_name)
        logger.warning("%s circuit open. Skipping call for %.1fs.", service_name, remaining)
        return None

    try:
        response = requests.request(method, url, **kwargs)
        if response.status_code == 429:
            MONITOR.increment("rate_limits", service_name)
            _register_service_failure(service_name)
            if attempt >= MAX_API_RETRIES:
                logger.error("%s rate limited after %s retries", service_name, MAX_API_RETRIES)
                return None
            wait = _compute_backoff_seconds(attempt, response.headers.get("Retry-After"))
            MONITOR.increment("retries", service_name)
            logger.warning(
                "%s rate limited. Waiting %.2fs (retry %s/%s)",
                service_name, wait, attempt + 1, MAX_API_RETRIES,
            )
            time.sleep(wait)
            return _http_request_with_retry(method, url, service_name, attempt + 1, **kwargs)
        if response.status_code >= 500:
            MONITOR.increment("server_errors", service_name)
            _register_service_failure(service_name)
            if attempt >= MAX_API_RETRIES:
                logger.error("%s server error %s after %s retries", service_name, response.status_code, MAX_API_RETRIES)
                return None
            wait = _compute_backoff_seconds(attempt)
            MONITOR.increment("retries", service_name)
            logger.warning("%s server error %s. Retrying in %.2fs...", service_name, response.status_code, wait)
            time.sleep(wait)
            return _http_request_with_retry(method, url, service_name, attempt + 1, **kwargs)
        _register_service_success(service_name)
        return response
    except requests.exceptions.RequestException as e:
        MONITOR.increment("network_errors", service_name)
        _register_service_failure(service_name)
        if attempt >= MAX_API_RETRIES:
            logger.error("%s request failed after %s retries: %s", service_name, MAX_API_RETRIES, e)
            return None
        wait = _compute_backoff_seconds(attempt)
        MONITOR.increment("retries", service_name)
        logger.warning("%s request failed (%s). Retrying in %.2fs...", service_name, e, wait)
        time.sleep(wait)
        return _http_request_with_retry(method, url, service_name, attempt + 1, **kwargs)


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
_configure_stdout()
_setup_logging()
_register_shutdown_handlers()

# CONFIGURATION
TOP_COINS_LIMIT = 20
GRID_LEVELS = 15
FEE_PERCENT = 0.15
TRADE_SIZE_USD = 1000.0
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
MAX_API_RETRIES = int(os.getenv("MAX_API_RETRIES", "5"))
API_RETRY_BACKOFF_BASE = float(os.getenv("API_RETRY_BACKOFF_BASE", "2"))
RETRY_JITTER_SECONDS = float(os.getenv("RETRY_JITTER_SECONDS", "0.3"))
MAX_BACKOFF_SECONDS = float(os.getenv("MAX_BACKOFF_SECONDS", "30"))
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
CIRCUIT_BREAKER_COOLDOWN_SECONDS = float(os.getenv("CIRCUIT_BREAKER_COOLDOWN_SECONDS", "60"))
METRICS_WINDOW_SIZE = int(os.getenv("METRICS_WINDOW_SIZE", "100"))
METRICS_REPORT_EVERY_CYCLES = int(os.getenv("METRICS_REPORT_EVERY_CYCLES", "10"))
COINGECKO_TIMEOUT_SECONDS = float(os.getenv("COINGECKO_TIMEOUT_SECONDS", "10"))
MAGICLABS_TIMEOUT_SECONDS = float(os.getenv("MAGICLABS_TIMEOUT_SECONDS", "15"))
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "60"))

# Load from environment variables (see .env.example).
# Register at https://www.coingecko.com/en/api to get a key.
# Leave empty to use public endpoint (rate limited).
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

MAGICLABS_JWT = os.getenv("MAGICLABS_JWT", "")
MAGICLABS_API_KEY = os.getenv("MAGICLABS_API_KEY", "")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free")
OPENROUTER_APP_URL = os.getenv("OPENROUTER_APP_URL", "https://github.com/softwaretech412/Trading-bot-with-Python")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "QUANT Grid Trader")

SLO_TARGETS_MS = {
    "MagicLabs.trending": float(os.getenv("TARGET_LATENCY_MAGICLABS_MS", "3000")),
    "CoinGecko.markets": float(os.getenv("TARGET_LATENCY_COINGECKO_MS", "2000")),
    "OpenRouter.chat": float(os.getenv("TARGET_LATENCY_OPENROUTER_MS", "15000")),
    "Cycle.total": float(os.getenv("TARGET_LATENCY_CYCLE_MS", str(CHECK_INTERVAL * 1000))),
}

CIRCUIT_BREAKERS = {}
MONITOR = RuntimeMonitor(METRICS_WINDOW_SIZE, METRICS_REPORT_EVERY_CYCLES)


def _is_interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def parse_ai_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    return json.loads(text)


def make_coingecko_request(url, params=None):
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    response = _http_request_with_retry(
        "GET", url, "CoinGecko", headers=headers, params=params, timeout=COINGECKO_TIMEOUT_SECONDS,
    )
    if response is None or not response.ok:
        if response is not None:
            logger.error("CoinGecko error %s: %s", response.status_code, response.text[:200])
        return None
    return response.json()


def _fetch_trending_coins():
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MAGICLABS_JWT}",
        "X-Magic-API-Key": MAGICLABS_API_KEY,
        "X-Magic-Chain": "CARDANO",
    }
    response = _http_request_with_retry(
        "GET",
        "https://tee.express.magiclabs.network/v2/coins/cardano/trending",
        "MagicLabs",
        headers=headers,
        timeout=MAGICLABS_TIMEOUT_SECONDS,
    )
    if response is None or not response.ok:
        if response is not None:
            logger.error("MagicLabs error %s: %s", response.status_code, response.text[:200])
        return None
    content_type = response.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        return json.loads(response.text)
    if content_type.startswith("application/yaml"):
        return yaml.load(response.text, Loader=yaml.Loader)
    return None


def get_trending_coins():
    try:
        result, _ = _timed_call("MagicLabs.trending", _fetch_trending_coins)
        return result
    except Exception:
        return None


def get_crypto_price(crypto_id):
    # Gets real-time price of trending coins.
    # Uses api from https://www.coingecko.com/ preferred for accuracy.
    data = make_coingecko_request(f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=usd")
    if data and crypto_id in data:
        return data[crypto_id]["usd"]
    return None


def get_market_data(coin_ids):
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

    def _fetch():
        return make_coingecko_request("https://api.coingecko.com/api/v3/coins/markets", params)

    try:
        data, _ = _timed_call("CoinGecko.markets", _fetch)
    except Exception:
        return {}
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


def _call_openrouter(prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_APP_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a quantitative trading strategy evaluator. Respond with valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 1024,
    }
    response = _http_request_with_retry(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        "OpenRouter",
        headers=headers,
        json=body,
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    if response is None:
        return None
    if response.status_code == 404:
        detail = response.json().get("error", {}).get("message", response.text)
        logger.error("OpenRouter model not found: %s", detail)
        logger.error("Update OPENROUTER_MODEL in .env (see https://openrouter.ai/models)")
        return None
    if response.status_code == 402:
        logger.error("OpenRouter: insufficient credits. Use a :free model or add credits at https://openrouter.ai/credits")
        return None
    if not response.ok:
        logger.error("OpenRouter error %s: %s", response.status_code, response.text[:200])
        return None
    return response.json()


def analyze_with_ai(data):
    if not data:
        return {}

    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set. Using rule-based fallback.")
        return _fallback_ai_decisions(data)

    prompt = f"""
You are a quantitative trading advisor. Given the following grid trading strategy parameters for multiple cryptocurrencies, evaluate each and decide whether to deploy a grid trading strategy (APPROVE) or not (REJECT) based on viability, volatility, fee drag, and trend.

Data: {json.dumps(data, indent=2)}

Return a JSON object with a key for each symbol and a verdict "APPROVE" or "REJECT", along with a brief reason.
Example: {{"BTC": {{"verdict": "APPROVE", "reason": "Viable volatility and net profit positive"}}, ...}}
Return JSON only. No markdown.
"""
    try:
        result, _ = _timed_call("OpenRouter.chat", _call_openrouter, prompt)
        if result is None:
            MONITOR.increment("ai_fallbacks")
            return _fallback_ai_decisions(data)
        ai_output = result["choices"][0]["message"]["content"]
        decisions = parse_ai_json(ai_output)
        logger.info("AI decisions received for %s assets (model: %s).", len(decisions), OPENROUTER_MODEL)
        return decisions
    except Exception as e:
        logger.error("OpenRouter error: %s. Falling back to rule-based decisions.", e)
        MONITOR.increment("ai_fallbacks")
        return _fallback_ai_decisions(data)


def _fallback_ai_decisions(data):
    decisions = {}
    for item in data:
        sym = item["symbol"]
        decisions[sym] = {
            "verdict": "APPROVE" if item["grid_strategy"]["viable"] else "REJECT",
            "reason": "Fallback due to API error"
        }
    return decisions


def resolve_selected_symbols(symbols, symbol_to_id):
    env_selection = os.getenv("SELECTED_SYMBOLS", "").strip()
    if env_selection:
        if env_selection.lower() == "all":
            return symbols
        parts = [p.strip() for p in env_selection.split(",") if p.strip()]
        if parts and all(p.isdigit() for p in parts):
            selected = []
            for part in parts:
                idx = int(part) - 1
                if 0 <= idx < len(symbols):
                    selected.append(symbols[idx])
                else:
                    logger.warning("Index %s out of range. Skipping.", part)
            return selected or symbols
        selected = []
        for part in parts:
            sym = part.upper()
            if sym in symbol_to_id:
                selected.append(sym)
            else:
                logger.warning("Unknown symbol '%s'. Skipping.", part)
        return selected or symbols

    if _is_interactive():
        logger.info("Available Cryptocurrencies:")
        for idx, sym in enumerate(symbols, start=1):
            logger.info("%s. %s", idx, sym)
        try:
            selection = input("\nEnter the numbers of the cryptocurrencies you want to track (comma-separated, or 'all' for all): ")
            if selection.lower().strip() == "all":
                return symbols
            indices = [int(i.strip()) for i in selection.split(",") if i.strip()]
            return [symbols[i - 1] for i in indices if 1 <= i <= len(symbols)] or symbols
        except (ValueError, EOFError):
            logger.warning("Invalid or missing input. Using all symbols.")
            return symbols

    logger.info("Non-interactive mode. Using all symbols (set SELECTED_SYMBOLS to override).")
    return symbols


def resolve_thresholds(selected_symbols):
    thresholds = {sym: 0.0 for sym in selected_symbols}
    raw = os.getenv("THRESHOLDS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            for sym, val in parsed.items():
                key = sym.upper()
                if key in thresholds:
                    thresholds[key] = float(val)
        except (json.JSONDecodeError, ValueError, TypeError):
            for part in raw.split(","):
                if ":" not in part:
                    continue
                sym, val = part.split(":", 1)
                key = sym.strip().upper()
                if key in thresholds:
                    try:
                        thresholds[key] = float(val.strip())
                    except ValueError:
                        thresholds[key] = 0.0
        return thresholds

    if _is_interactive():
        for sym in selected_symbols:
            try:
                thresholds[sym] = float(input(f"Enter {sym} buy/sell threshold (optional, press Enter to skip): ") or "0")
            except (ValueError, EOFError):
                thresholds[sym] = 0.0
    return thresholds


def _print_portfolio(portfolio):
    logger.info("Final portfolio:")
    for asset, balance in portfolio.items():
        logger.info("  %s: %s", asset, balance)


def _run_trading_cycle(selected_ids, portfolio):
    market_data = get_market_data(selected_ids)
    if not market_data:
        logger.warning("No market data received. Skipping cycle.")
        return 0

    structured_data = build_structured_json(market_data)
    logger.info("Grid metrics computed for %s coins.", len(structured_data))

    for item in structured_data:
        sym = item["symbol"]
        name = item["name"]
        md = item["market_data"]
        gs = item["grid_strategy"]
        logger.info("Asset: %s (%s)", sym, name)
        logger.info("    Price: $%s | Volatility: %s%%", md['current_price'], md['volatility_pct'])
        logger.info("    Grid Bounds: [$%s - $%s]", gs['suggested_lower'], gs['suggested_upper'])
        logger.info(
            "    Net Step Profit: %s%% [%s]",
            gs['net_step_profit_pct'],
            'OK' if gs['net_step_profit_pct'] > 0 else 'NO',
        )
        logger.info("    Distance to Lower: %s%%", gs['distance_to_lower_pct'])
        logger.info("    7d Trend: %s%% | 24h Mom: %s%%", md['change_7d_pct'], md['change_24h_pct'])
        logger.info("    Viable: %s", gs['viable'])

    decisions = analyze_with_ai(structured_data)
    logger.info("AI decisions ready for %s assets.", len(decisions))

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
                logger.info(
                    "AI APPROVED: Buying %s %s @ $%s (USD left: $%s)",
                    f"{amount:.6f}", sym, f"{price:.2f}", f"{portfolio['USD']:.2f}",
                )
                executed += 1
            else:
                logger.warning("Insufficient USD to buy %s (balance: $%s)", sym, f"{usd_balance:.2f}")
        else:
            reason = decisions.get(sym, {}).get("reason", "No AI decision")
            logger.info("AI REJECTED %s: %s", sym, reason)

    logger.info("Executed %s trades this cycle.", executed)
    return executed


def main():
    global shutdown_requested

    logger.info("==============================")
    logger.info("QUANT Grid Trader Advisor")
    logger.info("==============================")

    if not COINGECKO_API_KEY:
        logger.warning(
            "No CoinGecko API key set. Using public endpoint. Increase CHECK_INTERVAL to reduce rate limits."
        )

    logger.info("Fetching real-time latest trending cryptocurrency coins...")
    coin_data = get_trending_coins()

    if not coin_data:
        logger.error("No crypto data received.")
        return

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
        logger.error("Unknown data format received.")
        return

    if not symbol_to_id:
        logger.error("No symbol-id mapping found.")
        return

    logger.info("Loaded %s cryptocurrencies.", len(symbol_to_id))

    symbols = sorted(symbol_to_id.keys())
    selected_symbols = resolve_selected_symbols(symbols, symbol_to_id)
    selected_ids = [symbol_to_id[sym] for sym in selected_symbols]
    logger.info("Tracking %s coins: %s", len(selected_ids), ', '.join(selected_symbols))

    resolve_thresholds(selected_symbols)

    portfolio = {"USD": 50000.0}
    for sym in selected_symbols:
        portfolio[sym] = 0.0

    logger.info("Starting continuous trading loop (Ctrl+C or SIGTERM to stop)...")

    while not shutdown_requested:
        cycle_start = time.time()
        try:
            _run_trading_cycle(selected_ids, portfolio)
        except Exception as e:
            logger.error("Cycle failed unexpectedly: %s", e, exc_info=True)

        elapsed = time.time() - cycle_start
        wait = max(0, CHECK_INTERVAL - elapsed)
        MONITOR.record_latency("Cycle.total", elapsed * 1000)
        cycle_target = SLO_TARGETS_MS.get("Cycle.total")
        if cycle_target and elapsed * 1000 > cycle_target:
            logger.warning(
                "SLO breach for Cycle.total: %.0fms (target %.0fms)",
                elapsed * 1000, cycle_target
            )
        metrics = _get_system_metrics()
        if metrics:
            logger.info(
                "Cycle done in %.2fs. CPU: %s%% | Memory: %sMB | Waiting %.2fs...",
                elapsed, metrics["cpu_percent"], metrics["memory_mb"], wait,
            )
        else:
            logger.info("Cycle done in %.2fs. Waiting %.2fs...", elapsed, wait)
        MONITOR.log_cycle_summary()

        if shutdown_requested:
            break

        slept = 0.0
        while slept < wait and not shutdown_requested:
            chunk = min(1.0, wait - slept)
            time.sleep(chunk)
            slept += chunk

    logger.info("Shutdown requested. Exiting gracefully.")
    _print_portfolio(portfolio)
    logger.info("Goodbye.")

if __name__ == "__main__":
    main()