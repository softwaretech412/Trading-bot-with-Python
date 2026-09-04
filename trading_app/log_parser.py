from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple


LOG_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\]\[(?P<source>[^\]]+)\]\[(?P<level>[^\]]+)\]\s(?P<message>.*)$"
)

ASSET_PATTERN = re.compile(r"^Asset:\s(?P<symbol>[A-Z0-9_]+)\s\((?P<name>.+)\)$")
PRICE_VOL_PATTERN = re.compile(r"^\s+Price:\s\$(?P<price>.+)\s\|\sVolatility:\s(?P<vol>.+)%$")
BOUNDS_PATTERN = re.compile(r"^\s+Grid Bounds:\s\[\$(?P<lower>.+)\s-\s\$(?P<upper>.+)\]$")
NET_STEP_PATTERN = re.compile(r"^\s+Net Step Profit:\s(?P<value>.+)%\s\[(?P<status>[A-Z]+)\]$")
DISTANCE_PATTERN = re.compile(r"^\s+Distance to Lower:\s(?P<value>.+)%$")
TREND_PATTERN = re.compile(r"^\s+7d Trend:\s(?P<trend7d>.+)%\s\|\s24h Mom:\s(?P<mom24h>.+)%$")
VIABLE_PATTERN = re.compile(r"^\s+Viable:\s(?P<value>True|False)$")
REJECT_PATTERN = re.compile(r"^AI REJECTED\s(?P<symbol>[A-Z0-9_]+):\s(?P<reason>.*)$")
BUY_PATTERN = re.compile(
    r"^AI APPROVED:\sBuying\s(?P<amount>.+)\s(?P<symbol>[A-Z0-9_]+)\s@\s\$(?P<price>.+)\s\(USD left:\s\$(?P<usd_left>.+)\)$"
)


@dataclass
class LogRecord:
    timestamp: str
    source: str
    level: str
    message: str
    raw: str


@dataclass
class CoinSnapshot:
    symbol: str
    name: str = ""
    price: str = ""
    volatility_pct: str = ""
    lower_bound: str = ""
    upper_bound: str = ""
    net_step_profit_pct: str = ""
    net_step_status: str = ""
    distance_to_lower_pct: str = ""
    trend_7d_pct: str = ""
    momentum_24h_pct: str = ""
    viable: str = ""
    verdict: str = ""
    reason: str = ""
    buy_amount: str = ""
    buy_price: str = ""
    usd_left: str = ""
    last_updated_ts: str = ""
    cycle_id: int = 0
    all_messages: List[str] = field(default_factory=list)


class RuntimeLogParser:
    def __init__(self) -> None:
        self.current_cycle = 0
        self._current_symbol: Optional[str] = None
        self.coin_snapshots: Dict[str, CoinSnapshot] = {}

    def parse_line(self, line: str) -> Tuple[Optional[LogRecord], bool]:
        parsed = LOG_PATTERN.match(line.strip())
        if not parsed:
            return None, False

        record = LogRecord(
            timestamp=parsed.group("timestamp"),
            source=parsed.group("source"),
            level=parsed.group("level"),
            message=parsed.group("message"),
            raw=line.rstrip("\n"),
        )
        updated = self._consume_message(record)
        return record, updated

    def _coin(self, symbol: str) -> CoinSnapshot:
        if symbol not in self.coin_snapshots:
            self.coin_snapshots[symbol] = CoinSnapshot(symbol=symbol)
        return self.coin_snapshots[symbol]

    def _consume_message(self, record: LogRecord) -> bool:
        message = record.message

        if "Starting continuous trading loop" in message:
            self.current_cycle = 0
        if "Grid metrics computed for" in message:
            self.current_cycle += 1

        match = ASSET_PATTERN.match(message)
        if match:
            symbol = match.group("symbol")
            coin = self._coin(symbol)
            coin.name = match.group("name")
            coin.last_updated_ts = record.timestamp
            coin.cycle_id = self.current_cycle
            coin.all_messages.append(message)
            self._current_symbol = symbol
            return True

        if self._current_symbol:
            coin = self._coin(self._current_symbol)
            if self._match_detail(message, coin):
                coin.last_updated_ts = record.timestamp
                coin.cycle_id = self.current_cycle
                coin.all_messages.append(message)
                return True

        reject = REJECT_PATTERN.match(message)
        if reject:
            symbol = reject.group("symbol")
            coin = self._coin(symbol)
            coin.verdict = "REJECT"
            coin.reason = reject.group("reason")
            coin.last_updated_ts = record.timestamp
            coin.cycle_id = self.current_cycle
            coin.all_messages.append(message)
            return True

        buy = BUY_PATTERN.match(message)
        if buy:
            symbol = buy.group("symbol")
            coin = self._coin(symbol)
            coin.verdict = "APPROVE"
            coin.reason = "AI approved buy order."
            coin.buy_amount = buy.group("amount")
            coin.buy_price = buy.group("price")
            coin.usd_left = buy.group("usd_left")
            coin.last_updated_ts = record.timestamp
            coin.cycle_id = self.current_cycle
            coin.all_messages.append(message)
            return True

        return False

    def _match_detail(self, message: str, coin: CoinSnapshot) -> bool:
        price = PRICE_VOL_PATTERN.match(message)
        if price:
            coin.price = price.group("price")
            coin.volatility_pct = price.group("vol")
            return True

        bounds = BOUNDS_PATTERN.match(message)
        if bounds:
            coin.lower_bound = bounds.group("lower")
            coin.upper_bound = bounds.group("upper")
            return True

        net = NET_STEP_PATTERN.match(message)
        if net:
            coin.net_step_profit_pct = net.group("value")
            coin.net_step_status = net.group("status")
            return True

        distance = DISTANCE_PATTERN.match(message)
        if distance:
            coin.distance_to_lower_pct = distance.group("value")
            return True

        trend = TREND_PATTERN.match(message)
        if trend:
            coin.trend_7d_pct = trend.group("trend7d")
            coin.momentum_24h_pct = trend.group("mom24h")
            return True

        viable = VIABLE_PATTERN.match(message)
        if viable:
            coin.viable = viable.group("value")
            return True

        return False
