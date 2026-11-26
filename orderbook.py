from decimal import Decimal
from sortedcontainers import SortedDict
from models import BookUpdate, Pricelevel


class OrderBook:
    def __init__(self, symbol: str):
        self.symbol = symbol  # which market this book is for example BTCUSDT
        # Maps price -> quantity
        self.bids: SortedDict[Decimal, Decimal] = SortedDict()
        self.asks: SortedDict[Decimal, Decimal] = SortedDict()

    # Bids are sorted ascending by default in SortedDict, highest bid is at index -1
    def apply_update(self, update: BookUpdate) -> None:
        # Process Bids
        for level in update.bids:
            if level.quantity == Decimal("0"):
                self.bids.pop(level.price, None)
            else:
                self.bids[level.price] = level.quantity

        for level in update.asks:
            if level.quantity == Decimal("0"):
                self.asks.pop(level.price, None)
            else:
                self.asks[level.price] = level.quantity

    @property
    def best_bid(self) -> Pricelevel | None:
        if not self.bids:
            return None
        price, quantity = self.bids.peekitem(-1)
        # return highest bid as  pricelevel
        return Pricelevel(price=price, quantity=quantity)

    @property
    def best_ask(self) -> Pricelevel | None:
        if not self.asks:
            return None
        price, quantity = self.asks.peekitem(0)
        # todo: return lowest ask as a pricelevel
        return Pricelevel(price=price, quantity=quantity)

    @property
    def spread(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return ask.price - bid.price

    @property
    def mid_price(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return (bid.price + ask.price) / Decimal("2")
