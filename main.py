import asyncio
from decimal import Decimal

from binance import BinanceClient
from orderbook import OrderBook
from models import BookUpdate, Pricelevel


# --- Quick smoke-test of the OrderBook (runs once at startup) ---
def smoke_test() -> None:
    book = OrderBook(symbol="BTCUSDT")
    update = BookUpdate(
        symbol="BTCUSDT",
        timestamp=1714300800000,
        first_update_id=1,
        final_update_id=2,
        bids=[Pricelevel(price=Decimal("42000.50"), quantity=Decimal("1.5"))],
        asks=[Pricelevel(price=Decimal("42001.00"), quantity=Decimal("2.0"))],
    ) 
    update2 = BookUpdate(
        symbol="ETHUSDT",
        timestamp=1927300800000,
        first_update_id=1,
        final_update_id=2,
        bids=[Pricelevel(price=Decimal("3000.50"), quantity=Decimal("1.5"))],
        asks=[Pricelevel(price=Decimal("3002.00"), quantity=Decimal("2.0"))],
    )

    book.apply_update(update)
    print("smoke_test → best_bid:", book.best_bid)
    print("smoke_test → best_ask:", book.best_ask)
    print("smoke_test → spread:  ", book.spread)
    print("smoke_test → mid:     ", book.mid_price)

    try:
        book.apply_update(update2)
    except ValueError as error:
        print("error")

    book.apply_update(update)
    print("smoke_test → best_bid:", book.best_bid)
    print("smoke_test → best_ask:", book.best_ask)
    print("smoke_test → spread:  ", book.spread)


# --- Entry point ---
# asyncio.run() starts the event loop and runs our async main coroutine.
# Everything async happens inside here.
async def main() -> None:
    smoke_test()
    book = OrderBook(symbol="BTCUSDT")
    client = BinanceClient(symbol="BTCUSDT", book=book)
    await client.start()  # runs forever, reconnects on disconnect


if __name__ == "__main__":
    asyncio.run(main())
