# Cross-Exchange Arbitrage Scanner

A local browser dashboard that compares shared spot markets across multiple exchanges. It estimates whether a marketable buy on one venue and a sell on another remains positive after walking visible order-book depth, taker fees, configured rebalancing costs, and a configurable latency-risk buffer.

## What it is

This is a recruiter-demo research scanner, not a profitable strategy claim. Its current live path uses CCXT's rate-limited REST snapshots. The separate `compare_prices.py` is a two-exchange WebSocket example for BTC/USDT; the dashboard is the multi-pair/multi-venue path.

**This is an analytics and paper-analysis demo. It never submits orders.** A market-data snapshot is not a fill guarantee. Cross-exchange execution assumes inventory is already available on both venues; transfers are for periodic rebalancing and can take time.

## Run locally

```bash
cp .env.example .env
uv sync
uv run uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>.

The scanner uses public exchange APIs. It starts with Binance, Kraken, OKX, Bybit, and Gate.io, discovers active shared spot markets quoted in USD, USDT, or USDC, ranks them by reported 24-hour quote volume, then scans the top `ARB_MAX_PAIRS`. Failed or unavailable exchanges are shown in the dashboard and the other feeds continue. Configure additional CCXT exchange IDs in `ARB_EXCHANGES`.

## Cost model

- **Depth/slippage:** the engine spends the quote budget across ask levels, sells the resulting base quantity across bid levels, and rejects the trade when either side lacks enough visible quantity. The displayed depth impact is diagnostic; the VWAP gross spread already includes that impact, so do not subtract it twice.
- **Trading fees:** CCXT market fee metadata is used when available. Set `ARB_TAKER_FEES_JSON` or use the dashboard to override it with each account's taker rate. Values are decimal fractions: `0.001` means 0.10%. Fee schedules can depend on account tier, pair, region, promotions, and fee currency; confirm the actual charged fee in your account.
- **Rebalancing:** `ARB_REBALANCE_COST_QUOTE` is an estimated total cost per opportunity in the quote asset. Set it from the withdrawal route and frequency you expect. The scanner does not transfer assets.
- **Latency risk:** the dashboard accepts a user-calibrated risk buffer in basis points per second and multiplies it by the larger measured order-book request time. Zero means this cost is not modeled. Calibrate it from recorded market moves and end-to-end paper-order latency; it is not an exchange or network fee.
- **Freshness:** cross-venue snapshots beyond `ARB_MAX_BOOK_SKEW_MS` are discarded. API request times and scan-cycle time are displayed.

The default order budget is 100 quote units. Change it in the dashboard or set `ARB_QUOTE_BUDGET`. The quote currency is shown for every result; USD, USDT, and USDC are not assumed to be perfectly interchangeable.

## Proxy

To use 0dcloud's local HTTP proxy, set this in `.env`:

```dotenv
ARB_HTTP_PROXY=http://127.0.0.1:17891
```

CCXT routes its REST requests through this HTTP proxy. On a VPS, leave it empty and use the VPS's direct network route unless you intentionally configure a proxy. A proxy adds latency, so measure both routes before choosing one.

## Configure exchanges and pairs

Set `ARB_EXCHANGES` to comma-separated CCXT IDs, for example `binance,kraken,okx,bybit,gate,coinbase,kucoin,mexc`. The scanner only compares a symbol when at least two enabled exchanges list it as an active spot market with the same quote currency. `ARB_MAX_PAIRS` caps the volume-ranked common-market list.

## Run in Docker

```bash
docker build -t arb-scanner .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 arb-scanner
```

Keep the dashboard private. Before remote access, put it behind TLS and authentication; the cost model can be changed through the UI. Do not add exchange API keys to this public-market-data service.

## VPS and speed plan

This version uses rate-limited asynchronous REST snapshots. It is not a high-frequency trading system. The next speed step is persistent exchange-specific WebSocket L2 feeds with sequence/checksum validation, a local book per market, reconnect/resync logic, and recorded-feed replay. Only then benchmark the signal-to-paper-order path.

For deployment, benchmark candidate VPS regions against every exchange endpoint and choose from measured median and tail latency, packet loss, and stable WebSocket uptime. There may be no single region closest to every venue. Start with one process, no VPN/proxy, persistent clock-synchronized logging, and paper-only execution; add a second region only if measurements justify the complexity. Python optimizes the first correct scanner well enough for research; network and exchange latency dominate this REST prototype.

## Current scope

- Five configurable exchange adapters through CCXT; more can be enabled by ID.
- Dynamic common spot-pair discovery and volume ranking.
- Two-way cross-exchange comparisons, order-book depth walking, fees, configurable rebalancing and latency-risk costs.
- Local dashboard with filtering, positive-only view, live health, per-venue average REST request time, and editable cost assumptions.
- No authenticated trading, testnet order submission, balance/inventory checks, automated transfer, historical backtest, or guaranteed profitability.
