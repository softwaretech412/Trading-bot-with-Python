# QUANT Grid Trader Advisor

Python trading bot that fetches trending Cardano coins (Magic Labs), pulls market data from CoinGecko, evaluates grid-trading viability, and uses an AI layer (OpenRouter) to approve or reject simulated trades.

> **Note:** Order execution is **simulated** in a virtual portfolio. Real exchange API integration is not included in this version.

## Requirements

- Python 3.10+
- Internet access for Magic Labs, CoinGecko, and OpenRouter APIs

## Install

**Windows (PowerShell):**

```powershell
python -m pip install -r requirements.txt
# or run the setup script (also creates .env):
.\setup.ps1
```

**Linux/macOS:**

```bash
pip install -r requirements.txt
```

> On Windows, if `pip` is not recognized, use `python -m pip` instead. The project's `.vscode/settings.json` adds Python to the terminal PATH automatically in Cursor/VS Code.

## Configuration

Copy the example env file and add your API keys:

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
```

Edit `.env` with your keys. **Never commit `.env` to git.**

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COINGECKO_API_KEY` | Recommended | (empty) | CoinGecko API key. Without it, public rate limits apply. |
| `MAGICLABS_JWT` | Yes | — | Magic Labs JWT for trending coins |
| `MAGICLABS_API_KEY` | Yes | — | Magic Labs API key |
| `OPENROUTER_API_KEY` | Yes | — | OpenRouter key for AI decisions |
| `OPENROUTER_MODEL` | No | `minimax/minimax-m3:free` | Model ID from [openrouter.ai/models](https://openrouter.ai/models) |
| `OPENROUTER_APP_URL` | No | GitHub repo URL | Sent as `HTTP-Referer` header |
| `OPENROUTER_APP_NAME` | No | `QUANT Grid Trader` | Sent as `X-Title` header |
| `SELECTED_SYMBOLS` | No | (auto) | `all`, comma-separated symbols (`LINK,SNEK`), or indices (`1,4`) |
| `THRESHOLDS` | No | `0` for all | JSON `{"LINK":0.5}` or `LINK:0,SNEK:1` |
| `CHECK_INTERVAL` | No | `30` | Seconds between trading cycles |
| `MAX_API_RETRIES` | No | `5` | Max retries for external requests before fail/skip |
| `API_RETRY_BACKOFF_BASE` | No | `2` | Exponential backoff base (seconds) |
| `RETRY_JITTER_SECONDS` | No | `0.3` | Random jitter added to backoff to avoid retry storms |
| `MAX_BACKOFF_SECONDS` | No | `30` | Hard cap for retry wait duration |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | No | `5` | Consecutive failures before a service circuit opens |
| `CIRCUIT_BREAKER_COOLDOWN_SECONDS` | No | `60` | Cooldown duration when circuit is open |
| `COINGECKO_TIMEOUT_SECONDS` | No | `10` | Timeout for CoinGecko HTTP requests |
| `MAGICLABS_TIMEOUT_SECONDS` | No | `15` | Timeout for Magic Labs HTTP requests |
| `OPENROUTER_TIMEOUT_SECONDS` | No | `60` | Timeout for OpenRouter requests |
| `TARGET_LATENCY_MAGICLABS_MS` | No | `3000` | SLO target for Magic Labs trending call |
| `TARGET_LATENCY_COINGECKO_MS` | No | `2000` | SLO target for CoinGecko markets call |
| `TARGET_LATENCY_OPENROUTER_MS` | No | `15000` | SLO target for OpenRouter chat call |
| `TARGET_LATENCY_CYCLE_MS` | No | `30000` | SLO target for a full cycle |
| `METRICS_WINDOW_SIZE` | No | `100` | Number of recent latency samples retained |
| `METRICS_REPORT_EVERY_CYCLES` | No | `10` | How often reliability summary is logged |
| `ALERT_COOLDOWN_SECONDS` | No | `300` | Minimum seconds between repeated alerts with same key |
| `ALERT_SLO_BREACH_THRESHOLD` | No | `3` | Number of SLO breaches before alert is raised |
| `ALERT_CIRCUIT_OPEN_THRESHOLD` | No | `1` | Number of circuit-open events before alert is raised |
| `ALERT_AI_FALLBACK_THRESHOLD` | No | `2` | Number of AI fallback events before alert is raised |
| `ALERT_LOW_USD_THRESHOLD` | No | `10000` | Alert when virtual USD balance drops below threshold |
| `ALERT_WEBHOOK_URL` | No | (empty) | Optional webhook endpoint for external alert notifications |
| `ALERT_WEBHOOK_TIMEOUT_SECONDS` | No | `3` | Timeout for webhook alert delivery |
| `OPENROUTER_TEMPERATURE` | No | `0` | Locks deterministic model behavior |
| `OPENROUTER_TOP_P` | No | `0.1` | Narrows token sampling for stable output |
| `OPENROUTER_MAX_TOKENS` | No | `512` | Caps response size to reduce drift/latency |
| `OPENROUTER_FREQUENCY_PENALTY` | No | `0` | Frequency penalty passed to OpenRouter |
| `OPENROUTER_PRESENCE_PENALTY` | No | `0` | Presence penalty passed to OpenRouter |
| `OPENROUTER_SEED` | No | `42` | Seed value for repeatable model output (provider-dependent) |
| `LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### API key sources

- CoinGecko: https://www.coingecko.com/en/api
- Magic Labs: https://magic.link/
- OpenRouter: https://openrouter.ai/keys

## Run

### Unattended / 24/7 (recommended)

In scripts, CI, or Cursor, the bot auto-selects all coins with no prompts:

```bash
python quant-py-trading-bot.py
```

### Tuned baseline profile

From observed runs (CoinGecko ~= 66ms, OpenRouter ~= 4-5s, full cycle ~= 4-5s), this project now supports a conservative 24/7 profile:

- `CHECK_INTERVAL=20`
- `COINGECKO_TIMEOUT_SECONDS=6`
- `MAGICLABS_TIMEOUT_SECONDS=10`
- `OPENROUTER_TIMEOUT_SECONDS=20`
- `MAX_API_RETRIES=4`
- `API_RETRY_BACKOFF_BASE=1.8`
- `RETRY_JITTER_SECONDS=0.5`
- `MAX_BACKOFF_SECONDS=12`
- `TARGET_LATENCY_COINGECKO_MS=1200`
- `TARGET_LATENCY_OPENROUTER_MS=7000`
- `TARGET_LATENCY_CYCLE_MS=12000`

Optional: set symbols explicitly:

```bash
# Windows PowerShell
$env:SELECTED_SYMBOLS="LINK,SNEK"
python quant-py-trading-bot.py
```

### Interactive terminal

Run in a real terminal **without** `SELECTED_SYMBOLS` set to be prompted for coin selection and thresholds.

Stop the bot with `Ctrl+C`. Final portfolio balances are printed on shutdown.

## What the bot does each cycle

1. Fetch trending coins (Magic Labs)
2. Fetch market data (CoinGecko)
3. Compute grid strategy metrics (volatility, bounds, net step profit)
4. Ask AI (OpenRouter) for APPROVE/REJECT per coin
5. Execute simulated buys in a virtual USD portfolio
6. Wait `CHECK_INTERVAL` seconds and repeat

## Reading output & performance

The bot uses **structured logging** to stdout:

```
[2026-09-04 08:00:01][QUANT ENGINE][INFO] API CoinGecko.markets completed in 842ms
[2026-09-04 08:00:05][QUANT ENGINE][INFO] API OpenRouter.chat completed in 3210ms
[2026-09-04 08:00:05][QUANT ENGINE][INFO] Cycle done in 6.45s. CPU: 12.3% | Memory: 45.2MB | Waiting 23.55s...
```

### Per-cycle metrics

Each cycle logs:
- **API latency** (ms) for Magic Labs, CoinGecko, and OpenRouter
- **Cycle duration** (seconds)
- **CPU %** and **memory (MB)** via `psutil`
- **Wait time** until the next cycle
- **SLO breaches** when latest API/cycle latency exceeds configured targets
- **Reliability summary** every `METRICS_REPORT_EVERY_CYCLES` cycles (retries, rate limits, network/server faults, circuit opens/skips, AI fallbacks)

Set `LOG_LEVEL=DEBUG` for more detail.

### Documented latency targets

| Call | Target | Notes |
|------|--------|-------|
| Magic Labs trending | < 3s | 15s timeout configured |
| CoinGecko markets | < 2s | Retries on 429/5xx with backoff |
| OpenRouter AI | < 15s | 60s timeout; falls back to rules on failure |
| Full cycle | < `CHECK_INTERVAL` | Typically 3–10s depending on coin count |

### Success indicators

```
Loaded 15 cryptocurrencies.
Grid metrics computed for 6 coins.
AI decisions received for 6 assets (model: minimax/minimax-m3:free).
```

### Warnings (non-fatal)

| Message | Meaning |
|---------|---------|
| `OpenRouter model not found` | Update `OPENROUTER_MODEL` in `.env` |
| `Insufficient credits` | Use a `:free` model or add credits |
| `Falling back to rule-based decisions` | AI failed; uses `viable` flag instead |
| `No market data received` | CoinGecko returned nothing; cycle skipped |
| `Insufficient USD to buy` | Virtual portfolio balance exhausted |
| `SLO breach for ...` | Request/cycle exceeded target latency; tune timeouts/retries/model |
| `circuit open` | Service hit repeated faults; requests temporarily skipped to avoid cascade failure |
| `[ALERT][LOW_USD] ...` | Virtual cash dropped below `ALERT_LOW_USD_THRESHOLD` |
| `[ALERT][AI_FALLBACKS] ...` | AI fallback count crossed `ALERT_AI_FALLBACK_THRESHOLD` |
| `[ALERT][SLO_BREACH] ...` | Repeated latency breaches crossed `ALERT_SLO_BREACH_THRESHOLD` |

## Error handling

- **All APIs:** Retries up to `MAX_API_RETRIES` on rate limits (429), server errors (5xx), and network faults
- **Backoff strategy:** Exponential backoff with jitter (`RETRY_JITTER_SECONDS`) capped by `MAX_BACKOFF_SECONDS`
- **Circuit breaker:** Opens per-service after repeated failures and auto-recovers after cooldown
- **OpenRouter:** Falls back to rule-based APPROVE/REJECT if AI fails
- **Model stability controls:** Temperature/top-p/token/seed controls are configurable and pinned by default
- **AI response validation:** Non-JSON, missing symbols, invalid verdicts, and empty reasons are normalized via safe fallback
- **Main loop:** Each cycle is isolated — a failure logs the error and continues on the next interval
- **Shutdown:** Handles `Ctrl+C` (SIGINT) and `SIGTERM` for graceful exit with portfolio summary
- **Cross-platform:** ASCII status markers (`[OK]`/`[NO]`) and UTF-8 stdout for Windows compatibility

## Project layout

```
Trading Bot/
├── quant-py-trading-bot.py   # Main application
├── requirements.txt          # Python dependencies
├── .env.example              # Env template (safe to commit)
├── .env                      # Your secrets (gitignored)
└── README.md                 # This file
```

## Security

- Store all API keys in `.env` only
- Rotate any keys that were ever committed to git
- `.env` is listed in `.gitignore`

## Limitations

- Trading is **simulated** only (no real exchange orders)
- Not all trending coins have CoinGecko market data (typically ~6 of 15)
- `THRESHOLDS` are collected but not yet applied in buy/sell logic
- Free OpenRouter models may rate-limit under heavy use

## License

Private project — see repository owner for terms.
