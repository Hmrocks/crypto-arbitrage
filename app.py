"""Local web dashboard for the multi-exchange opportunity scanner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from scanner import MultiExchangeScanner


ROOT = Path(__file__).parent
scanner = MultiExchangeScanner()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scanner.run())
    app.state.scanner_task = task
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await scanner.close()


app = FastAPI(title="Cross-Exchange Arbitrage Scanner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    return scanner.state.to_dict()


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return {
        "quote_budget": str(scanner.quote_budget),
        "rebalance_cost": str(scanner.rebalance_cost),
        "latency_buffer_bps": str(scanner.latency_buffer_bps),
        "max_book_skew_ms": str(scanner.max_book_skew_ms),
        "taker_fees": {name: str(rate) for name, rate in scanner.fee_overrides.items()},
    }


def read_decimal(payload: dict[str, Any], name: str, current: Decimal, *, positive: bool = False) -> Decimal:
    if name not in payload:
        return current
    try:
        value = Decimal(str(payload[name]))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} must be a decimal number")
    if not value.is_finite() or (positive and value <= 0) or (not positive and value < 0):
        constraint = "greater than zero" if positive else "non-negative"
        raise HTTPException(status_code=422, detail=f"{name} must be {constraint}")
    return value


@app.post("/api/settings")
async def update_settings(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Settings must be a JSON object")
    # Validate everything first; then apply the update atomically.
    quote_budget = read_decimal(payload, "quote_budget", scanner.quote_budget, positive=True)
    rebalance_cost = read_decimal(payload, "rebalance_cost", scanner.rebalance_cost)
    latency_buffer_bps = read_decimal(
        payload, "latency_buffer_bps", scanner.latency_buffer_bps
    )
    max_book_skew_ms = read_decimal(
        payload, "max_book_skew_ms", scanner.max_book_skew_ms, positive=True
    )
    fees = scanner.fee_overrides
    if "taker_fees" in payload:
        raw_fees = payload["taker_fees"]
        if not isinstance(raw_fees, dict):
            raise HTTPException(status_code=422, detail="taker_fees must be a JSON object")
        fees = {}
        for name, raw_rate in raw_fees.items():
            rate = read_decimal({"fee": raw_rate}, "fee", Decimal("0"))
            if rate >= 1:
                raise HTTPException(status_code=422, detail=f"Fee for {name} must be less than 1")
            fees[str(name).lower()] = rate
    scanner.quote_budget = quote_budget
    scanner.rebalance_cost = rebalance_cost
    scanner.latency_buffer_bps = latency_buffer_bps
    scanner.max_book_skew_ms = max_book_skew_ms
    scanner.fee_overrides = fees
    return await get_settings()
