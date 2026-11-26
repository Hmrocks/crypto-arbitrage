"""Concurrent multi-exchange spot order-book scanner (public market data only)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

import ccxt.async_support as ccxt
from dotenv import load_dotenv


load_dotenv()


ZERO = Decimal("0")
QUOTE_ASSETS = {"USDT", "USDC", "USD"}
STABLE_BASE_ASSETS = {
    "USDC",
    "USDT",
    "USD",
    "DAI",
    "TUSD",
    "FDUSD",
    "BUSD",
    "USDE",
    "USDD",
    "PYUSD",
    "USDP",
    "RLUSD",
    "USD1",
    "USTC",
}
DEFAULT_EXCHANGES = ("binance", "kraken", "okx", "bybit", "gate")
LIQUID_BASE_PRIORITY = (
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "DOGE",
    "ADA",
    "TRX",
    "LINK",
    "AVAX",
    "LTC",
    "BCH",
    "DOT",
    "TON",
    "SUI",
    "SHIB",
    "NEAR",
    "UNI",
    "APT",
    "ARB",
)


def env_decimal(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a decimal number") from error
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def configured_fees() -> dict[str, Decimal]:
    """Optional per-exchange taker fee overrides, e.g. {"binance": "0.001"}."""
    raw = os.getenv("ARB_TAKER_FEES_JSON", "{}")
    values = json.loads(raw)
    fees = {str(name).lower(): Decimal(str(rate)) for name, rate in values.items()}
    for name, rate in fees.items():
        if not rate.is_finite() or rate < ZERO or rate >= 1:
            raise ValueError(f"Taker fee for {name} must be between 0 and 1")
    return fees


@dataclass(frozen=True)
class Book:
    exchange: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    received_at: float
    latency_ms: float
    taker_fee: Decimal | None


@dataclass
class ScannerState:
    status: str = "starting"
    updated_at: str | None = None
    cycle_ms: float | None = None
    exchange_ids: tuple[str, ...] = ()
    exchange_latency_ms: dict[str, float] | None = None
    pairs_scanned: int = 0
    books_received: int = 0
    errors: list[str] | None = None
    opportunities: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decimal_levels(raw: list[list[float]]) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple(
        (Decimal(str(level[0])), Decimal(str(level[1])))
        for level in raw
        if Decimal(str(level[1])) > ZERO
    )


def walk(
    levels: tuple[tuple[Decimal, Decimal], ...], amount: Decimal
) -> tuple[Decimal, Decimal]:
    """Return base filled and quote total by consuming best levels first."""
    remaining = amount
    filled = ZERO
    quote_total = ZERO
    for price, available in levels:
        take = min(remaining, available)
        filled += take
        quote_total += take * price
        remaining -= take
        if remaining <= ZERO:
            break
    return filled, quote_total


def buy_amount_for_quote(
    asks: tuple[tuple[Decimal, Decimal], ...], quote_budget: Decimal
) -> tuple[Decimal, Decimal]:
    """Spend up to a quote budget across asks; return (base bought, quote spent)."""
    remaining_quote = quote_budget
    base_bought = ZERO
    quote_spent = ZERO
    for price, available in asks:
        take = min(available, remaining_quote / price)
        base_bought += take
        level_cost = take * price
        quote_spent += level_cost
        remaining_quote -= level_cost
        if remaining_quote <= ZERO:
            break
    return base_bought, quote_spent


def evaluate_pair(
    buy: Book,
    sell: Book,
    quote_budget: Decimal,
    rebalance_cost: Decimal,
    latency_buffer_bps: Decimal,
) -> dict[str, Any] | None:
    """Estimate a marketable cross-exchange trade at configured size."""
    if not buy.asks or not sell.bids:
        return None
    if (
        buy.symbol != sell.symbol
        or buy.symbol.split("/")[-1] != sell.symbol.split("/")[-1]
    ):
        return None
    if buy.exchange == sell.exchange:
        return None
    if buy.taker_fee is None or sell.taker_fee is None:
        return {
            "symbol": buy.symbol,
            "buy_exchange": buy.exchange,
            "sell_exchange": sell.exchange,
            "executable": False,
            "reason": "Taker fee unknown; add an account-specific fee override",
            "net_quote": None,
            "amount_unit": buy.symbol.split("/")[0],
            "quote": buy.symbol.split("/")[1],
            "buy_latency_ms": round(buy.latency_ms, 2),
            "sell_latency_ms": round(sell.latency_ms, 2),
            "book_skew_ms": round(abs(buy.received_at - sell.received_at) * 1000, 2),
        }
    amount, buy_cost = buy_amount_for_quote(buy.asks, quote_budget)
    sell_filled, sell_proceeds = walk(sell.bids, amount)
    if buy_cost < quote_budget or sell_filled < amount:
        return {
            "symbol": buy.symbol,
            "buy_exchange": buy.exchange,
            "sell_exchange": sell.exchange,
            "executable": False,
            "reason": "Insufficient displayed depth for configured quote budget",
            "buy_depth_quote": str(buy_cost),
            "sell_depth_base": str(sell_filled),
            "requested_amount": str(amount),
            "amount_unit": buy.symbol.split("/")[0],
            "requested_quote": str(quote_budget),
            "net_quote": None,
            "quote": buy.symbol.split("/")[1],
            "buy_latency_ms": round(buy.latency_ms, 2),
            "sell_latency_ms": round(sell.latency_ms, 2),
            "book_skew_ms": round(abs(buy.received_at - sell.received_at) * 1000, 2),
        }

    gross = sell_proceeds - buy_cost
    buy_fee = buy_cost * buy.taker_fee
    sell_fee = sell_proceeds * sell.taker_fee
    slippage = (
        buy_cost - amount * buy.asks[0][0] + amount * sell.bids[0][0] - sell_proceeds
    )
    total_latency = Decimal(str(max(buy.latency_ms, sell.latency_ms))) / Decimal("1000")
    latency_cost = quote_budget * latency_buffer_bps / Decimal("10000") * total_latency
    net = gross - buy_fee - sell_fee - rebalance_cost - latency_cost
    return {
        "symbol": buy.symbol,
        "buy_exchange": buy.exchange,
        "sell_exchange": sell.exchange,
        "executable": True,
        "requested_amount": str(amount),
        "amount_unit": buy.symbol.split("/")[0],
        "requested_quote": str(quote_budget),
        "gross_quote": str(gross),
        "fees_quote": str(buy_fee + sell_fee),
        "slippage_quote": str(slippage),
        "rebalance_cost_quote": str(rebalance_cost),
        "latency_buffer_quote": str(latency_cost),
        "net_quote": str(net),
        "quote": buy.symbol.split("/")[1],
        "buy_vwap": str(buy_cost / amount),
        "sell_vwap": str(sell_proceeds / amount),
        "buy_fee_rate": str(buy.taker_fee),
        "sell_fee_rate": str(sell.taker_fee),
        "buy_latency_ms": round(buy.latency_ms, 2),
        "sell_latency_ms": round(sell.latency_ms, 2),
        "book_skew_ms": round(abs(buy.received_at - sell.received_at) * 1000, 2),
    }


class MultiExchangeScanner:
    def __init__(self) -> None:
        raw_ids = os.getenv("ARB_EXCHANGES", ",".join(DEFAULT_EXCHANGES))
        self.exchange_ids = tuple(
            dict.fromkeys(
                item.strip().lower() for item in raw_ids.split(",") if item.strip()
            )
        )
        self.max_pairs = max(1, int(os.getenv("ARB_MAX_PAIRS", "20")))
        self.depth = max(5, min(100, int(os.getenv("ARB_BOOK_DEPTH", "20"))))
        self.interval = max(3.0, float(os.getenv("ARB_SCAN_INTERVAL_SECONDS", "10")))
        self.quote_budget = env_decimal("ARB_QUOTE_BUDGET", "100")
        self.rebalance_cost = env_decimal("ARB_REBALANCE_COST_QUOTE", "0")
        self.latency_buffer_bps = env_decimal("ARB_LATENCY_BUFFER_BPS", "0")
        self.max_book_skew_ms = env_decimal("ARB_MAX_BOOK_SKEW_MS", "1500")
        if (
            self.quote_budget <= ZERO
            or self.rebalance_cost < ZERO
            or self.latency_buffer_bps < ZERO
        ):
            raise ValueError(
                "Budget must be positive; cost and latency buffer must be non-negative"
            )
        self.fee_overrides = configured_fees()
        self.exchanges: dict[str, Any] = {}
        self.markets: dict[str, dict[str, Any]] = {}
        self.symbols: list[str] = []
        self.state = ScannerState(
            exchange_ids=self.exchange_ids, errors=[], opportunities=[]
        )

    async def initialize(self) -> None:
        for exchange_id in self.exchange_ids:
            try:
                exchange_class = getattr(ccxt, exchange_id)
                exchange_config = {"enableRateLimit": True, "timeout": 12000}
                http_proxy = os.getenv("ARB_HTTP_PROXY")
                if http_proxy:
                    exchange_config["aiohttp_proxy"] = http_proxy
                exchange = exchange_class(exchange_config)
                if exchange.has.get("spot") is False:
                    await exchange.close()
                    continue
                self.exchanges[exchange_id] = exchange
            except Exception as error:
                self.state.errors.append(f"{exchange_id}: init failed: {error}")

        loaded = await asyncio.gather(
            *(exchange.load_markets() for exchange in self.exchanges.values()),
            return_exceptions=True,
        )
        for (exchange_id, exchange), result in zip(self.exchanges.items(), loaded):
            if isinstance(result, BaseException):
                self.state.errors.append(f"{exchange_id}: market list failed: {result}")
                continue
            self.markets[exchange_id] = result

        markets_by_exchange: dict[str, set[str]] = {}
        for exchange_id, markets in self.markets.items():
            markets_by_exchange[exchange_id] = {
                symbol
                for symbol, market in markets.items()
                if market.get("active") is not False
                and market.get("spot") is not False
                and market.get("base") not in STABLE_BASE_ASSETS
                and market.get("base")
                and market.get("quote") in QUOTE_ASSETS
                and "/" in symbol
                and ":" not in symbol
            }
        support: dict[str, int] = {}
        for symbols in markets_by_exchange.values():
            for symbol in symbols:
                support[symbol] = support.get(symbol, 0) + 1
        common = sorted(symbol for symbol, count in support.items() if count >= 2)
        if not common:
            self.state.errors.append(
                "No shared active spot symbols found across enabled exchanges"
            )
            return

        volumes = await asyncio.gather(
            *(self._fetch_tickers(exchange_id) for exchange_id in self.markets),
            return_exceptions=True,
        )
        common_set = set(common)
        ranked_volume: dict[str, Decimal] = {}
        for item in volumes:
            if isinstance(item, dict):
                for symbol, ticker in item.items():
                    if symbol not in common_set:
                        continue
                    volume = ticker.get("quoteVolume")
                    if volume is not None:
                        ranked_volume[symbol] = ranked_volume.get(
                            symbol, ZERO
                        ) + Decimal(str(volume))
        if any(volume > ZERO for volume in ranked_volume.values()):
            self.symbols = sorted(
                common,
                key=lambda symbol: ranked_volume.get(symbol, ZERO),
                reverse=True,
            )[: self.max_pairs]
        else:
            base_rank = {base: rank for rank, base in enumerate(LIQUID_BASE_PRIORITY)}
            self.symbols = sorted(
                common,
                key=lambda symbol: (
                    base_rank.get(symbol.split("/")[0], len(base_rank)),
                    symbol,
                ),
            )[: self.max_pairs]
        self.state.pairs_scanned = len(self.symbols)
        if not self.symbols:
            self.state.errors.append("No pair symbols selected")

    async def _fetch_tickers(self, exchange_id: str) -> dict[str, Any]:
        exchange = self.exchanges[exchange_id]
        if not exchange.has.get("fetchTickers"):
            return {}
        try:
            # A single bulk request avoids very long URLs built from thousands
            # of common symbols. Only common symbols are kept for ranking.
            return await exchange.fetch_tickers()
        except Exception as error:
            self.state.errors.append(
                f"{exchange_id}: volume ranking unavailable: {error}"
            )
            return {}

    async def fetch_book(self, exchange_id: str, symbol: str) -> Book:
        exchange = self.exchanges[exchange_id]
        market = self.markets[exchange_id][symbol]
        started = time.perf_counter()
        raw_book = await exchange.fetch_order_book(symbol, limit=self.depth)
        latency_ms = (time.perf_counter() - started) * 1000
        received_at = time.time()
        taker_fee = self.fee_overrides.get(exchange_id)
        if taker_fee is None and market.get("taker") is not None:
            taker_fee = Decimal(str(market["taker"]))
        return Book(
            exchange=exchange_id,
            symbol=symbol,
            bids=decimal_levels(raw_book.get("bids", [])),
            asks=decimal_levels(raw_book.get("asks", [])),
            received_at=received_at,
            latency_ms=latency_ms,
            taker_fee=taker_fee,
        )

    async def scan_once(self) -> None:
        cycle_started = time.perf_counter()
        errors: list[str] = []
        pairs = [
            (exchange_id, symbol)
            for exchange_id in self.markets
            for symbol in self.symbols
            if symbol in self.markets[exchange_id]
        ]
        semaphore = asyncio.Semaphore(12)

        async def limited_fetch(exchange_id: str, symbol: str) -> Book | None:
            async with semaphore:
                try:
                    return await self.fetch_book(exchange_id, symbol)
                except Exception as error:
                    errors.append(f"{exchange_id} {symbol}: {error}")
                    return None

        results = await asyncio.gather(
            *(limited_fetch(exchange_id, symbol) for exchange_id, symbol in pairs)
        )
        books: dict[str, dict[str, Book]] = {}
        latency_samples: dict[str, list[float]] = {}
        for book in results:
            if book is not None:
                books.setdefault(book.symbol, {})[book.exchange] = book
                latency_samples.setdefault(book.exchange, []).append(book.latency_ms)

        # Average successful order-book request time, grouped by venue.
        exchange_latency_ms = {
            exchange_id: round(sum(samples) / len(samples), 2)
            for exchange_id, samples in latency_samples.items()
        }

        opportunities: list[dict[str, Any]] = []
        online_exchanges = {book.exchange for book in results if book is not None}
        stale_count = 0
        for symbol, symbol_books in books.items():
            for first, second in combinations(symbol_books.values(), 2):
                skew_ms = abs(first.received_at - second.received_at) * 1000
                if Decimal(str(skew_ms)) > self.max_book_skew_ms:
                    stale_count += 1
                    continue
                for buy, sell in ((first, second), (second, first)):
                    result = evaluate_pair(
                        buy,
                        sell,
                        self.quote_budget,
                        self.rebalance_cost,
                        self.latency_buffer_bps,
                    )
                    if result is not None:
                        opportunities.append(result)

        opportunities.sort(
            key=lambda row: Decimal(row["net_quote"] or "-Infinity"), reverse=True
        )
        cycle_ms = (time.perf_counter() - cycle_started) * 1000
        self.state = ScannerState(
            status="live" if len(online_exchanges) >= 2 else "degraded",
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            cycle_ms=round(cycle_ms, 2),
            exchange_ids=tuple(sorted(online_exchanges)),
            exchange_latency_ms=exchange_latency_ms,
            pairs_scanned=len(self.symbols),
            books_received=len(results) - sum(item is None for item in results),
            errors=errors[:30],
            opportunities=opportunities,
        )
        self.state.errors.extend(self._init_errors)
        if stale_count:
            self.state.errors.append(
                f"Skipped {stale_count} exchange comparisons with stale books"
            )

    async def run(self) -> None:
        await self.initialize()
        self._init_errors = list(self.state.errors or [])
        while True:
            await self.scan_once()
            await asyncio.sleep(self.interval)

    async def close(self) -> None:
        await asyncio.gather(
            *(exchange.close() for exchange in self.exchanges.values()),
            return_exceptions=True,
        )
