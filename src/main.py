"""LeadBridge: complete 5-stage Apify Actor entrypoint.

Stages:
  1. Auto-Profiler (URL crawl + strictly grounded Verified Capabilities Ledger)
  2. Multi-Source Async Complaint Harvester (parallel + conditional Pass 2 fallback)
  3. Deterministic Noise, Own-Brand & Recency Filter ($0 compute)
  4. Batched LLM Solvability Hard-Gate & Winability Scorer
  5. Conditional Dual Ban-Safe Drafting, Apify PPE Billing & REPORT.html Dashboard
"""

from __future__ import annotations

import asyncio
import logging
import os

from apify import Actor, Event
from dotenv import load_dotenv
from pydantic import ValidationError

from src.brain import score_complaints_batched
from src.collectors import harvest_complaints
from src.drafter import draft_hot_leads
from src.filters import filter_complaints
from src.profiler import build_product_profile
from src.radar import build_competitor_radar
from src.revenue import compute_revenue_scenario
from src.report import render_executive_report_html
from src.schemas import ActorInput, DatasetLeadRecord

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")


def _is_ppe_active() -> bool:
    """Detect whether Apify Pay-Per-Event billing is active (Cloud PPE or local test mode).

    Asks the SDK for the effective pricing model. Checking for the APIFY_ACTOR_PRICING_INFO
    env var is wrong: the platform sets it for free Actors too, which made every charge
    "fail" and silently blocked hot leads from reaching the Dataset.
    """
    if os.getenv("ACTOR_TEST_PAY_PER_EVENT", "").strip().lower() in {"1", "true", "yes"}:
        return True
    try:
        return bool(Actor.get_charging_manager().get_pricing_info().is_pay_per_event)
    except Exception:
        # Actor not initialised (unit tests / plain scripts): no PPE.
        return False


async def safe_charge_event(event_name: str, count: int = 1) -> tuple[bool, bool]:
    """Charge an Apify PPE event safely.

    Returns:
        (allow_delivery, limit_reached)
        - In PPE mode: strictly enforces charged_count > 0 and stops at spending limit.
        - In standard local dev runs (no PPE env): allows delivery so local testing works seamlessly.
    """
    if count <= 0:
        return True, False

    ppe_active = _is_ppe_active()
    try:
        result = await Actor.charge(event_name=event_name, count=count)
        charged = getattr(result, "charged_count", 0)
        limit_reached = bool(getattr(result, "event_charge_limit_reached", False))
        if ppe_active:
            return (charged > 0), limit_reached
        # Free / non-PPE run: nothing is charged, so a spend limit can never stop delivery.
        return True, False
    except Exception as exc:
        Actor.log.debug("Actor.charge('%s') skipped in non-PPE mode: %s", event_name, exc)
        return (not ppe_active), False


async def save_qualified_hot_lead(record: DatasetLeadRecord) -> tuple[bool, bool]:
    """Charge PPE event 'high-intent-lead-drafted' and push the lead to the Dataset.

    Returns:
        (delivered, keep_going)
        - delivered: True only if the lead was actually pushed (billing allowed it).
        - keep_going: False if the user hit ACTOR_MAX_TOTAL_CHARGE_USD so the caller can exit gracefully.
    """
    allow_delivery, limit_reached = await safe_charge_event("high-intent-lead-drafted", count=1)
    if allow_delivery:
        await Actor.push_data(record.model_dump(mode="json"))
    if limit_reached:
        Actor.log.info(
            "User spending limit (ACTOR_MAX_TOTAL_CHARGE_USD) reached. Stopping lead billing gracefully."
        )
    return allow_delivery, not limit_reached


async def main() -> None:
    async with Actor:
        run_state: dict[str, object] = {"stage": "initializing"}

        # Graceful 30-second platform abort handler
        async def on_aborting() -> None:
            Actor.log.warning("Actor aborting at stage=%s — flushing STATUS...", run_state["stage"])
            await Actor.set_value("STATUS", {"state": "aborted_gracefully", **run_state})

        Actor.on(Event.ABORTING, on_aborting)

        raw_input = await Actor.get_input() or {}
        try:
            actor_input = ActorInput.model_validate(raw_input)
        except ValidationError as exc:
            # Plain-language message instead of a traceback (e.g. a product name typed into the URL field).
            problems = "; ".join(
                f"{'.'.join(str(x) for x in err['loc']) or 'input'}: {err['msg'].removeprefix('Value error, ')}"
                for err in exc.errors()
            )
            message = (
                f"Invalid input - {problems}. productUrl must be your website address, "
                "e.g. https://your-product.com (not the product name)."
            )
            Actor.log.error(message)
            await Actor.set_value("STATUS", {"state": "failed_input", "message": message})
            await Actor.fail(status_message=message[:500])
            return
        Actor.log.info(
            "Starting 5-stage pipeline | product=%s url=%s sources=%s lookback=%dd",
            actor_input.productName or "(read from website)",
            actor_input.productUrl,
            actor_input.sources,
            actor_input.lookbackDays,
        )

        # --- STAGE 1: Auto-Profiler ---
        run_state["stage"] = "stage_1_profiling"
        try:
            profile = await build_product_profile(actor_input)
        except ValueError as exc:
            # e.g. the site could not be read and no productDescription was given: tell the user how to fix it.
            await Actor.set_value("STATUS", {"state": "failed_input", "message": str(exc)})
            await Actor.fail(status_message=str(exc)[:500])
            return
        await Actor.set_value("PRODUCT_PROFILE", profile.model_dump(mode="json"))
        _, limit_hit = await safe_charge_event("product-profile-analyzed", count=1)
        if limit_hit:
            Actor.log.info("Spending limit reached after Stage 1 profiling. Exiting gracefully.")
            return

        # --- STAGE 2: Multi-Source Async Complaint Harvester ---
        run_state["stage"] = "stage_2_harvesting"
        stage2 = await harvest_complaints(profile=profile, actor_input=actor_input)
        await Actor.set_value("STAGE2_TELEMETRY", stage2.model_dump(mode="json"))

        # --- STAGE 3: Deterministic Noise & Own-Brand Filter ($0 Compute) ---
        run_state["stage"] = "stage_3_filtering"
        stage3 = filter_complaints(
            complaints=stage2.complaints,
            profile=profile,
            actor_input=actor_input,
        )
        await Actor.set_value("STAGE3_FILTER_REPORT", stage3.model_dump(mode="json"))

        # --- STAGE 4: Batched LLM Solvability Hard-Gate & Winability Scorer ---
        run_state["stage"] = "stage_4_scoring"
        stage4 = await score_complaints_batched(
            complaints=stage3.clean_complaints,
            profile=profile,
            actor_input=actor_input,
        )
        await Actor.set_value("STAGE4_SCORED_REPORT", stage4.model_dump(mode="json"))
        if stage4.total_evaluated > 0:
            await safe_charge_event("complaint-evaluated", count=stage4.total_evaluated)

        # --- STAGE 5A: Conditional Dual Ban-Safe Outreach Drafting ---
        # Drafting and the Competitor Pain Radar both only need Stage 4, so run them concurrently.
        run_state["stage"] = "stage_5_drafting_and_radar"
        stage5, radar = await asyncio.gather(
            draft_hot_leads(stage4=stage4, profile=profile, actor_input=actor_input),
            build_competitor_radar(stage4=stage4, profile=profile, actor_input=actor_input),
        )
        await Actor.set_value("COMPETITOR_RADAR", radar.model_dump(mode="json"))

        # --- STAGE 5B: Push Hot Leads (with PPE Spend Guard) + Warm/Gap Insights ---
        run_state["stage"] = "stage_5_publishing"
        pushed_hot = 0
        for hot_record in stage5.hot_lead_records:
            delivered, keep_going = await save_qualified_hot_lead(hot_record)
            if delivered:
                pushed_hot += 1
            if not keep_going:
                break

        # Warm signals & market gaps are saved without charging the hot-lead drafting event
        non_hot_records = stage5.warm_signal_records + stage5.market_gap_records
        if non_hot_records:
            await Actor.push_data([r.model_dump(mode="json") for r in non_hot_records])

        # --- STAGE 5C: Compile Self-Contained REPORT.html -> Key-Value Store ---
        report_html = render_executive_report_html(
            profile=profile,
            actor_input=actor_input,
            stage2_telemetry=stage2.telemetry,
            stage3=stage3,
            stage4=stage4,
            stage5=stage5,
            radar=radar,
        )
        await Actor.set_value("REPORT.html", report_html, content_type="text/html; charset=utf-8")

        summary = {
            "state": "completed",
            "product": profile.product_name,
            "rawHarvested": stage2.total_unique_count,
            "filteredOutFree": stage3.dropped_count,
            "evaluatedByLLM": stage4.total_evaluated,
            "hotLeadsDrafted": pushed_hot,
            "warmSignals": len(stage5.warm_signal_records),
            "unresolvedMarketGaps": len(stage5.market_gap_records),
            "lowPriority": len(stage4.low_priority),
            "rejectedNoise": len(stage4.discarded),
            "fallbackTriggered": stage2.fallback_triggered,
            "radarHeadline": radar.headline,
            "radarWeaknesses": len(radar.weaknesses),
            "radarPositioning": len(radar.positioning),
        }
        scenario = compute_revenue_scenario(pushed_hot, len(stage5.warm_signal_records), actor_input)
        if scenario:
            await Actor.set_value("REVENUE_SCENARIO", scenario.model_dump(mode="json"))
            summary["illustrativeRevenueUsd"] = scenario.illustrative_revenue_usd
            summary["revenueScenarioLabel"] = scenario.label
        await Actor.set_value("STATUS", summary)
        Actor.log.info("5-stage pipeline finished: %s", summary)


if __name__ == "__main__":
    asyncio.run(main())