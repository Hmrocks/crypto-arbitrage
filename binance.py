import asyncio
import json
import websockets

from decimal import Decimal
from orderbook import OrderBook

from models import BookUpdate, Pricelevel


class BinanceClient:
    def __init__(self, symbol: str, book: OrderBook):
        self.symbol = symbol.upper()
        # Binance stream URI — lowercase symbol required in URL
        self.uri = f"wss://stream.binance.com:9443/ws/{self.symbol.lower()}@depth"
        self.book = book  # store this

    # --- JSON parsing ---
    # Binance sends a raw JSON string like:
    # {"e":"depthUpdate","E":1714300800000,"s":"BTCUSDT",
    #  "U":157,"u":160,"b":[["42000.50","1.5"]],"a":[["42001.00","2.0"]]}
    #
    # We convert that into our BookUpdate dataclass so the rest of the app
    # never has to think about raw JSON again.
    def _parse(self, raw: str) -> BookUpdate:
        data = json.loads(raw)  # str -> dict

        def to_levels(pairs: list) -> list[Pricelevel]:
            # Each pair is ["price_str", "qty_str"]
            return [Pricelevel(price=Decimal(p), quantity=Decimal(q)) for p, q in pairs]

        return BookUpdate(
            symbol=data["s"],
            timestamp=data["E"],
            first_update_id=data["U"],
            final_update_id=data["u"],
            bids=to_levels(data["b"]),
            asks=to_levels(data["a"]),
        )

    # --- WebSocket loop ---
    # async def  →  this coroutine can be paused at 'await' points
    async def start(self) -> None:
        print(f"Connecting to {self.uri}...")

        while True:
            try:
                # async with  →  enter/exit the connection asynchronously
                async with websockets.connect(self.uri) as websocket:
                    print("Connected to Binance stream.")

                    # async for  →  each iteration awaits the next message
                    async for raw_message in websocket:
                        update = self._parse(raw_message)
                        self.book.apply_update(update)
                        print(
                            f"[{update.symbol}] "
                            f"bids={len(update.bids)}  "
                            f"asks={len(update.asks)}  "
                            f"last_id={update.final_update_id}"
                        )

            except Exception as e:
                print(f"Connection lost: {e}. Reconnecting in 5s...")
                # await  →  pause THIS coroutine without blocking the event loop
                await asyncio.sleep(5)
