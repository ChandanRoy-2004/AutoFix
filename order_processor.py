from models import Order
from tax_service import get_tax_rate_by_tier

def process_order(order: Order) -> dict:
    first_item = order.items[0]
    subtotal = sum(item.price * item.quantity for item in order.items)
    discount = 0.0
    if order.coupon_code == "SAVE20":
        discount = 20.0
    discounted_subtotal = subtotal - discount
    tax_rate = get_tax_rate_by_tier(order.customer_tier)
    tax_amount = discounted_subtotal * tax_rate
    return {
        "order_id": order.order_id,
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "tax": round(tax_amount, 2),
        "total": round(discounted_subtotal + tax_amount, 2)
    }
