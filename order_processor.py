from models import Order
from tax_service import get_tax_rate_by_tier

def process_order(order: Order) -> dict:
    items = order.items if order.items is not None else []
    subtotal = sum(item.price * item.quantity for item in items)
    
    discount = 0.0
    if order.coupon_code:
        if order.coupon_code.startswith("SAVE"):
            try:
                coupon_val = float(order.coupon_code[4:])
                discount = min(coupon_val, subtotal)
            except ValueError:
                discount = 0.0
    
    discounted_subtotal = max(0.0, subtotal - discount)
    
    tax_rate = get_tax_rate_by_tier(order.customer_tier)
    tax_amount = max(0.0, discounted_subtotal * tax_rate)
    
    total = discounted_subtotal + tax_amount
    
    return {
        "order_id": order.order_id,
        "subtotal": round(subtotal, 2),
        "discount": round(discount, 2),
        "tax": round(tax_amount, 2),
        "total": round(total, 2)
    }