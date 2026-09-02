def get_tax_rate_by_tier(customer_tier: str) -> float:
    rates = {
        "STANDARD": 0.10,
        "PREMIUM": 0.07,
        "VIP": 0.05
    }
    return rates.get(customer_tier.upper(), 0.10)
