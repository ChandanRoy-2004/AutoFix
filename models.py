from dataclasses import dataclass
from typing import List

@dataclass
class OrderItem:
    item_id: str
    price: float
    quantity: int

@dataclass
class Order:
    order_id: str
    customer_tier: str  # "STANDARD", "PREMIUM", "VIP"
    items: List[OrderItem]
    coupon_code: str = None