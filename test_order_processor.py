import pytest
from models import Order, OrderItem
from order_processor import process_order

def test_standard_order():
    items = [OrderItem("1", 50.0, 2), OrderItem("2", 20.0, 1)]
    order = Order(order_id="ORD-01", customer_tier="STANDARD", items=items)
    result = process_order(order)
    assert result["subtotal"] == 120.0
    assert result["tax"] == 12.0
    assert result["total"] == 132.0

def test_empty_order():
    order = Order(order_id="ORD-02", customer_tier="VIP", items=[])
    result = process_order(order)
    assert result["subtotal"] == 0.0
    assert result["total"] == 0.0

def test_coupon_exceeding_subtotal():
    items = [OrderItem("1", 10.0, 1)]
    order = Order(order_id="ORD-03", customer_tier="STANDARD", items=items, coupon_code="SAVE20")
    result = process_order(order)
    assert result["total"] >= 0.0
