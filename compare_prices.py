"""Compare executable BTC/USDT opportunities on Binance and Kraken."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TypeAlias

import websockets


DecimalLevels: TypeAlias = tuple[tuple[Decimal, Decimal], ...]
ZERO = Decimal("0")
SYMBOL = "BTC/USDT"
TRADE_SIZE_BTC = Decimal(os.getenv("ARB_TRADE_SIZE_BTC", "0.001"))
# Public starting taker rates. Override these with your account's actual rates.
BINANCE_TAKER_FEE = Decimal(os.getenv("BINANCE_TAKER_FEE", "0.001"))
KRAKEN_TAKER_FEE = Decimal(os.getenv("KRAKEN_TAKER_FEE", "0.008"))
# 0dcloud's local mixed HTTP/SOCKS port. Set ARB_PROXY= to connect directly.
PROXY_URL = os.getenv("ARB_PROXY", "socks5h://127.0.0.1:17891") or None
MAX_QUOTE_SKEW_SECONDS = Decimal("2")


@dataclass(frozen=True)
class OrderBookSnapshot:
    exchange: str
    bids: DecimalLevels  # Best price first: highest bid to lowest bid.
    asks: DecimalLevels  # Best price first: lowest ask to highest ask.
    received_at: datetime
    received_monotonic: float


def parse_levels(raw_levels: list[list[str]] | list[dict[str, object]]) -> DecimalLevels:
    """Convert either exchange's JSON price levels into Decimal pairs."""
    levels = []
    for level in raw_levels:
        if isinstance(level, dict):
            price, quantity = level["price"], level["qty"]
        else:
            price, quantity = level
        if Decimal(str(quantity)) > ZERO:
            levels.append((Decimal(str(price)), Decimal(str(quantity))))
    return tuple(levels)


def make_snapshot(exchange: str, bids: DecimalLevels, asks: DecimalLevels) -> OrderBookSnapshot:
    """Attach local receive times so we can detect badly mismatched snapshots."""
    return OrderBookSnapshot(
        exchange=exchange,
        bids=bids,
        asks=asks,
        received_at=datetime.now(timezone.utc),
        received_monotonic=time.monotonic(),
    )


async def get_binance_book() -> OrderBookSnapshot:
    """Read one Binance Spot top-20 depth update."""
    uri = "wss://data-stream.binance.vision/ws/btcusdt@depth20@100ms"
    async with websockets.connect(uri, proxy=PROXY_URL) as websocket:
        data = json.loads(await websocket.recv())
    bids = parse_levels(data["bids"])
    asks = parse_levels(data["asks"])
    return make_snapshot("Binance", bids, asks)


async def get_kraken_book() -> OrderBookSnapshot:
    """Subscribe to Kraken L2 and read its initial depth snapshot."""
    uri = "wss://ws.kraken.com/v2"
    async with websockets.connect(uri, proxy=PROXY_URL) as websocket:
        await websocket.send(json.dumps({
            "method": "subscribe",
            "params": {"channel": "book", "symbol": [SYMBOL], "depth": 10},
        }))
        while True:
            data = json.loads(await websocket.recv())
            if data.get("channel") == "book" and data.get("type") == "snapshot":
                book = data["data"][0]
                bids = parse_levels(book["bids"])
                asks = parse_levels(book["asks"])
                return make_snapshot("Kraken", bids, asks)


def simulate_fill(levels: DecimalLevels, requested_btc: Decimal) -> tuple[Decimal, Decimal]:
    """Walk price levels; return (filled BTC, quote-currency total)."""
    remaining = requested_btc
    filled = ZERO
    quote_total = ZERO
    for price, available_btc in levels:
        amount = min(remaining, available_btc)
        filled += amount
        quote_total += amount * price
        remaining -= amount
        if remaining == ZERO:
            break
    return filled, quote_total


def assess_direction(
    buy_book: OrderBookSnapshot,
    sell_book: OrderBookSnapshot,
    buy_fee_rate: Decimal,
    sell_fee_rate: Decimal,
    requested_btc: Decimal,
) -> list[str]:
    """Estimate a marketable buy/sell using visible depth and taker fees."""
    buy_filled, buy_cost = simulate_fill(buy_book.asks, requested_btc)
    sell_filled, sell_proceeds = simulate_fill(sell_book.bids, requested_btc)
    lines = [f"Buy {buy_book.exchange} / sell {sell_book.exchange}:"]
    if buy_filled < requested_btc or sell_filled < requested_btc:
        lines.append(
            f"  NOT FILLABLE for {requested_btc} BTC: visible depth supports "
            f"buy={buy_filled} BTC, sell={sell_filled} BTC."
        )
        return lines

    buy_vwap = buy_cost / requested_btc
    sell_vwap = sell_proceeds / requested_btc
    gross = sell_proceeds - buy_cost
    buy_fee = buy_cost * buy_fee_rate
    sell_fee = sell_proceeds * sell_fee_rate
    fees = buy_fee + sell_fee
    slippage = (
        buy_cost - requested_btc * buy_book.asks[0][0]
        + requested_btc * sell_book.bids[0][0] - sell_proceeds
    )
    net = gross - fees
    lines.extend([
        f"  Requested size: {requested_btc} BTC",
        f"  Buy VWAP: {buy_vwap:.2f} USDT; sell VWAP: {sell_vwap:.2f} USDT",
        f"  Gross spread: {gross:+.6f} USDT",
        f"  Estimated depth slippage: {slippage:.6f} USDT",
        f"  Estimated taker fees: {fees:.6f} USDT",
        f"  Estimated net: {net:+.6f} USDT",
    ])
    return lines


def build_report(binance: OrderBookSnapshot, kraken: OrderBookSnapshot) -> str:
    """Compare both directions and label the assumptions and data limits."""
    skew = abs(Decimal(str(binance.received_monotonic - kraken.received_monotonic)))
    output = [
        f"BTC/USDT depth comparison — {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Binance snapshot received: {binance.received_at.isoformat(timespec='milliseconds')}",
        f"Kraken snapshot received:  {kraken.received_at.isoformat(timespec='milliseconds')}",
        f"Snapshot receive-time skew: {skew:.3f}s",
        f"Requested trade size: {TRADE_SIZE_BTC} BTC",
        f"Taker fee assumptions: Binance={BINANCE_TAKER_FEE * 100}% / Kraken={KRAKEN_TAKER_FEE * 100}%",
        "Fees are estimated in USDT equivalent; confirm account rates and fee currency.",
        "Slippage is estimated by walking the received L2 depth; this is not a fill guarantee.",
        "",
    ]
    if skew > MAX_QUOTE_SKEW_SECONDS:
        output.append("WARNING: snapshots are over 2 seconds apart; treat estimates as stale.")
        output.append("")
    output.extend(assess_direction(
        binance, kraken, BINANCE_TAKER_FEE, KRAKEN_TAKER_FEE, TRADE_SIZE_BTC
    ))
    output.append("")
    output.extend(assess_direction(
        kraken, binance, KRAKEN_TAKER_FEE, BINANCE_TAKER_FEE, TRADE_SIZE_BTC
    ))
    return "\n".join(output)


async def main() -> None:
    """Fetch both public books, print the estimates, and save a report."""
    if TRADE_SIZE_BTC <= ZERO:
        raise SystemExit("ARB_TRADE_SIZE_BTC must be greater than zero.")
    if not (ZERO <= BINANCE_TAKER_FEE < 1 and ZERO <= KRAKEN_TAKER_FEE < 1):
        raise SystemExit("Taker fee settings must be decimal rates between 0 and 1.")
    try:
        binance, kraken = await asyncio.gather(get_binance_book(), get_kraken_book())
    except Exception as error:
        raise SystemExit(f"Could not fetch both order books: {error}") from error

    report = build_report(binance, kraken)
    print(report)
    Path("price_comparison.txt").write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
