from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    default: str
    field_type: str = "str"
    secret: bool = False


@dataclass(frozen=True)
class SettingGroup:
    name: str
    fields: List[SettingField]


SETTINGS_GROUPS: List[SettingGroup] = [
    SettingGroup(
        name="API Keys & App Identity",
        fields=[
            SettingField("COINGECKO_API_KEY", "CoinGecko API Key", "", secret=True),
            SettingField("MAGICLABS_JWT", "Magic Labs JWT", "", secret=True),
            SettingField("MAGICLABS_API_KEY", "Magic Labs API Key", "", secret=True),
            SettingField("OPENROUTER_API_KEY", "OpenRouter API Key", "", secret=True),
            SettingField("OPENROUTER_MODEL", "OpenRouter Model", "minimax/minimax-m3:free"),
            SettingField(
                "OPENROUTER_APP_URL",
                "OpenRouter App URL",
                "https://github.com/softwaretech412/Trading-bot-with-Python",
            ),
            SettingField("OPENROUTER_APP_NAME", "OpenRouter App Name", "QUANT Grid Trader"),
        ],
    ),
    SettingGroup(
        name="Trading Scope",
        fields=[
            SettingField("SELECTED_SYMBOLS", "Selected Symbols", "all"),
            SettingField("THRESHOLDS", "Thresholds", ""),
            SettingField("CHECK_INTERVAL", "Check Interval (s)", "30", field_type="int"),
        ],
    ),
    SettingGroup(
        name="Retry & Circuit Breaker",
        fields=[
            SettingField("MAX_API_RETRIES", "Max API Retries", "5", field_type="int"),
            SettingField("API_RETRY_BACKOFF_BASE", "Retry Backoff Base", "2", field_type="float"),
            SettingField("RETRY_JITTER_SECONDS", "Retry Jitter Seconds", "0.3", field_type="float"),
            SettingField("MAX_BACKOFF_SECONDS", "Max Backoff Seconds", "30", field_type="float"),
            SettingField(
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
                "Circuit Failure Threshold",
                "5",
                field_type="int",
            ),
            SettingField(
                "CIRCUIT_BREAKER_COOLDOWN_SECONDS",
                "Circuit Cooldown Seconds",
                "60",
                field_type="float",
            ),
        ],
    ),
    SettingGroup(
        name="Timeouts & SLO Targets",
        fields=[
            SettingField("COINGECKO_TIMEOUT_SECONDS", "CoinGecko Timeout Seconds", "10", field_type="float"),
            SettingField("MAGICLABS_TIMEOUT_SECONDS", "Magic Labs Timeout Seconds", "15", field_type="float"),
            SettingField("OPENROUTER_TIMEOUT_SECONDS", "OpenRouter Timeout Seconds", "60", field_type="float"),
            SettingField("TARGET_LATENCY_MAGICLABS_MS", "Target Latency MagicLabs (ms)", "3000", field_type="float"),
            SettingField("TARGET_LATENCY_COINGECKO_MS", "Target Latency CoinGecko (ms)", "2000", field_type="float"),
            SettingField("TARGET_LATENCY_OPENROUTER_MS", "Target Latency OpenRouter (ms)", "15000", field_type="float"),
            SettingField("TARGET_LATENCY_CYCLE_MS", "Target Latency Cycle (ms)", "30000", field_type="float"),
            SettingField("METRICS_WINDOW_SIZE", "Metrics Window Size", "100", field_type="int"),
            SettingField("METRICS_REPORT_EVERY_CYCLES", "Metrics Report Every Cycles", "10", field_type="int"),
        ],
    ),
    SettingGroup(
        name="Alerts",
        fields=[
            SettingField("ALERT_COOLDOWN_SECONDS", "Alert Cooldown Seconds", "300", field_type="float"),
            SettingField("ALERT_SLO_BREACH_THRESHOLD", "Alert SLO Breach Threshold", "3", field_type="int"),
            SettingField("ALERT_CIRCUIT_OPEN_THRESHOLD", "Alert Circuit Open Threshold", "1", field_type="int"),
            SettingField("ALERT_AI_FALLBACK_THRESHOLD", "Alert AI Fallback Threshold", "2", field_type="int"),
            SettingField("ALERT_LOW_USD_THRESHOLD", "Alert Low USD Threshold", "10000", field_type="float"),
            SettingField("ALERT_WEBHOOK_URL", "Alert Webhook URL", ""),
            SettingField("ALERT_WEBHOOK_TIMEOUT_SECONDS", "Alert Webhook Timeout Seconds", "3", field_type="float"),
        ],
    ),
    SettingGroup(
        name="AI Stabilization",
        fields=[
            SettingField("OPENROUTER_TEMPERATURE", "Temperature", "0", field_type="float"),
            SettingField("OPENROUTER_TOP_P", "Top P", "0.1", field_type="float"),
            SettingField("OPENROUTER_MAX_TOKENS", "Max Tokens", "512", field_type="int"),
            SettingField("OPENROUTER_FREQUENCY_PENALTY", "Frequency Penalty", "0", field_type="float"),
            SettingField("OPENROUTER_PRESENCE_PENALTY", "Presence Penalty", "0", field_type="float"),
            SettingField("OPENROUTER_SEED", "Seed", "42", field_type="int"),
        ],
    ),
    SettingGroup(
        name="System",
        fields=[
            SettingField("LOG_LEVEL", "Log Level", "INFO"),
        ],
    ),
]


def default_settings_map() -> Dict[str, str]:
    values: Dict[str, str] = {}
    for group in SETTINGS_GROUPS:
        for field in group.fields:
            values[field.key] = field.default
    return values
