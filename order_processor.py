from models import Order
from tax_service import get_tax_rate_by_tier

def process_order(order: Order) -> dict:
    subtotal = sum(item.price * item.quantity for item in order.items)
    
    discount = 0.0
    if order.coupon_code == "SAVE20":
        discount = min(20.0, subtotal)
    
    discounted_subtotal = max(0.0, subtotal - discount)
    
    tax_rate = get_tax_rate_by_tier(order.customer_tier)
    tax_amount = max(0.0, discounted_subtotal * tax_rate)
    
    return {
        "order_id": order.order_id,
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "tax": round(tax_amount, 2),
        "total": round(discounted_subtotal + tax_amount, 2)
    }