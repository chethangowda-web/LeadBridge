"""Revenue calculator: what the qualified opportunities could be worth, under the founder's assumptions.

This is an illustrative scenario, never a forecast. It reports *expected* customers as a fraction
(2 opportunities at 10% = 0.2 customers) instead of rounding up into customers that do not exist,
and it is only computed when the founder supplies their own deal value. The interactive version in
REPORT.html uses the same formula in JavaScript.
"""

from __future__ import annotations

from src.schemas import ActorInput, BillingPeriodType, RevenueScenario


def value_per_customer(deal_value_usd: float, billing_period: BillingPeriodType, contract_months: int) -> float:
    """Revenue from one converted customer over the assumed contract."""
    if billing_period == "monthly":
        return deal_value_usd * contract_months
    if billing_period == "yearly":
        return deal_value_usd * (contract_months / 12)
    return deal_value_usd  # one_time


def compute_revenue_scenario(
    act_now_count: int,
    monitor_count: int,
    actor_input: ActorInput,
) -> RevenueScenario | None:
    """Only Act-now opportunities count toward the scenario. Returns None without a deal value."""
    if not actor_input.dealValueUsd:
        return None
    per_customer = value_per_customer(
        actor_input.dealValueUsd, actor_input.billingPeriod, actor_input.contractMonths
    )
    expected = act_now_count * actor_input.assumedConversionRatePercent / 100
    return RevenueScenario(
        act_now_opportunities=act_now_count,
        monitor_opportunities=monitor_count,
        deal_value_usd=actor_input.dealValueUsd,
        billing_period=actor_input.billingPeriod,
        contract_months=actor_input.contractMonths,
        conversion_rate_percent=actor_input.assumedConversionRatePercent,
        value_per_customer_usd=round(per_customer, 2),
        expected_customers=round(expected, 2),
        illustrative_revenue_usd=round(expected * per_customer, 2),
    )
