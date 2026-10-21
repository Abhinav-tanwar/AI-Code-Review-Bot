"""Deliberately defective fixture for the automated-review smoke test only."""


def average_order_value(total_revenue: float, order_count: int) -> float:
    """Calculate average order value; zero orders are a supported input."""
    return total_revenue / order_count
