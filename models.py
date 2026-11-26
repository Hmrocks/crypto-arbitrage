from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Pricelevel:
    price: Decimal
    quantity: Decimal


# bookupdate is just a container for 1 incoming message
#
@dataclass
class BookUpdate:
    symbol: str
    timestamp: int
    first_update_id: int
    final_update_id: int
    bids: list[Pricelevel]
    asks: list[Pricelevel]


@dataclass(frozen=True)
class Box:
    xs: list[int]


b = Box(xs=[1, 2])
# Re-binding the attribute -> ERROR
# b.xs = [9, 9]        # dataclasses.FrozenInstanceError
# Mutating the list contents -> works
b.xs.append(3)  # OK
print(b.xs)  # [1, 2, 3]


# Run this and make sure both work without errors
p = Pricelevel(price=Decimal("42000.50"), quantity=Decimal("1.5"))

update = BookUpdate(
    symbol="BTCUSDT",
    timestamp=1714300800000,
    first_update_id=1234567,
    final_update_id=1234568,
    bids=[p],
    asks=[],
)

print(update)
