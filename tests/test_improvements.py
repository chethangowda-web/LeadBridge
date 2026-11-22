"""Tests for the lead-quality, billing-count and Claude-tier improvements ($0 cost)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.brain import score_complaints_batched
from src.filters import filter_complaints
from src.llm_router import LLMRouter
from src.schemas import (
    DatasetLeadRecord,
    LLMBatchEvaluationResponse,
    LLMComplaintEvaluation,
    LLMProfileExtraction,
)
from test_stage3_4 import NOW, _make_complaint, _sample_input, _sample_profile

POST_TEXT = (
    "Cannot reliably get local store prices from my Python scraping flow, "
    "missing perfume prices for several stores and results are flaky."
)


def _complaint_by(cid: str, author: str, title: str, text: str, source: str = "reddit", url: str | None = None):
    item = _make_complaint(cid, text, url=url, source=source, title=title, age_hours=5)
    item.author_handle = author
    return item


# ---------------------------------------------------------------------
# Stage 3: Reddit profile links and same-author cross-posts
# ---------------------------------------------------------------------


def _run_stage3(items):
    return filter_complaints(items, _sample_profile(), _sample_input(), now=NOW)


def test_stage3_drops_reddit_user_profile_links():
    profile_link = _complaint_by(
        "p1", "public_web", "Someone", POST_TEXT, source="google_serp",
        url="https://www.reddit.com/user/Federal_Row6029/",
    )
    real_post = _complaint_by("r1", "u/alice", "Real post", POST_TEXT + " Different words entirely here.")
    result = _run_stage3([profile_link, real_post])

    assert [c.id for c in result.clean_complaints] == ["r1"]
    assert result.drop_reason_counts == {"non_post_url": 1}
    assert result.dropped_items[0].matched_rule == "reddit_profile_not_post"


def test_stage3_drops_same_author_cross_post_but_keeps_other_authors():
    first = _complaint_by("a1", "u/Leading_Layer_546", "Cannot reliably get local store prices", POST_TEXT)
    cross = _complaint_by(
        "a2", "u/Leading_Layer_546", "Cannot reliably get local store prices in Bahrain",
        POST_TEXT + " Retail sites in Bahrain.",
    )
    other_author_same_text = _complaint_by("b1", "u/someone_else", "Cannot reliably get local store prices", POST_TEXT)
    result = _run_stage3([first, cross, other_author_same_text])

    kept = {c.id for c in result.clean_complaints}
    assert "a1" in kept
    assert "a2" not in kept
    dropped_rules = {d.id: d.matched_rule for d in result.dropped_items}
    assert dropped_rules["a2"].startswith("cross_post_same_author=")
    # b1 is word-for-word identical to a1 but by a different author, so the new author rule
    # must not fire; the pre-existing exact-content rule drops it instead.
    assert "b1" not in kept
    assert dropped_rules["b1"].startswith("fp=")


def test_stage3_does_not_treat_placeholder_authors_as_same_person():
    one = _complaint_by("g1", "public_web", "Vendor A pricing", "Vendor A raised prices again and our scraping bill doubled this quarter.", source="google_serp",
                        url="https://www.reddit.com/r/a/comments/g1/a/")
    two = _complaint_by("g2", "public_web", "Vendor A pricing thread", "Vendor A raised prices again and our scraping bill doubled this quarter for us.", source="google_serp",
                        url="https://www.reddit.com/r/b/comments/g2/b/")
    result = _run_stage3([one, two])
    assert not any(d.matched_rule.startswith("cross_post_same_author") for d in result.dropped_items)


# ---------------------------------------------------------------------
# Stage 4: low_priority is its own bucket, not "noise"
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage4_low_priority_is_not_counted_as_discarded():
    item = _make_complaint(
        "weak1", "Wave invoicing is a bit clunky sometimes but fine overall.", age_hours=400
    )
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            LLMBatchEvaluationResponse(
                evaluations=[
                    LLMComplaintEvaluation(
                        idx=0,
                        is_genuine_pain_or_seeking_alternative=True,
                        competitor_mentioned="Wave",
                        pain_category="ux_friction",
                        pain_summary="Mild friction using Wave.",
                        can_client_resolve=True,
                        matched_capability_indices=[1],
                        solvability_score=12,
                        urgency_score=5,
                    )
                ]
            ),
            "gemini:test",
        )
    )
    result = await score_complaints_batched(
        [item], _sample_profile(), _sample_input(), llm_router=router, now=NOW
    )

    assert len(result.low_priority) == 1
    assert result.low_priority[0].winability_score < 40
    assert result.discarded == []
    assert result.hot_leads == [] and result.warm_signals == []


# ---------------------------------------------------------------------
# Claude tier of the LLM router
# ---------------------------------------------------------------------


def _extraction() -> LLMProfileExtraction:
    return LLMProfileExtraction(
        product_category="Freelancer Invoicing SaaS",
        target_audience="Freelancers",
        verified_capabilities=["Flat $15/mo pricing", "Automated overdue reminders"],
        pain_points_solved=["Per-client tiers", "Chasing invoices"],
        competitors=["FreshBooks", "Wave"],
        competitor_pain_queries=["FreshBooks alternative", "Wave issues", "QuickBooks pricing"],
        category_problem_queries=["flat pricing invoicing", "overdue invoice reminders"],
    )


def _fake_anthropic(response):
    parse = AsyncMock(return_value=response)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    return client, parse


@pytest.mark.asyncio
async def test_claude_success_returns_parsed_output_and_uses_expected_request():
    expected = _extraction()
    client, parse = _fake_anthropic(
        SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=expected)
    )
    router = LLMRouter(anthropic_api_key="sk-ant-test")
    with patch("src.llm_router.AsyncAnthropic", return_value=client):
        result = await router._call_claude("sys prompt", "user prompt", LLMProfileExtraction)

    assert result is expected
    kwargs = parse.await_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is LLMProfileExtraction
    assert kwargs["system"] == "sys prompt"
    assert kwargs["messages"] == [{"role": "user", "content": "user prompt"}]
    assert "temperature" not in kwargs  # Opus 5.5 rejects sampling params


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason, needle", [("refusal", "refused"), ("max_tokens", "max_tokens")])
async def test_claude_refusal_and_truncation_raise(stop_reason, needle):
    client, _ = _fake_anthropic(
        SimpleNamespace(stop_reason=stop_reason, stop_details={"category": "cyber"}, parsed_output=None)
    )
    router = LLMRouter(anthropic_api_key="sk-ant-test")
    with patch("src.llm_router.AsyncAnthropic", return_value=client):
        with pytest.raises(ValueError, match=needle):
            await router._call_claude("s", "u", LLMProfileExtraction)


@pytest.mark.asyncio
async def test_claude_missing_parsed_output_raises():
    client, _ = _fake_anthropic(
        SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=None)
    )
    router = LLMRouter(anthropic_api_key="sk-ant-test")
    with patch("src.llm_router.AsyncAnthropic", return_value=client):
        with pytest.raises(ValueError, match="no parsable"):
            await router._call_claude("s", "u", LLMProfileExtraction)


@pytest.mark.asyncio
async def test_router_falls_through_claude_failure_to_openrouter(monkeypatch):
    # src.main loads .env at import; make sure no real Gemini key leaks into this test.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    router = LLMRouter(gemini_api_key=None, anthropic_api_key="k", openrouter_api_key="k")
    expected = _extraction()
    with (
        patch.object(router, "_call_claude", AsyncMock(side_effect=RuntimeError("529 overloaded"))),
        patch.object(router, "_call_openai_compat", AsyncMock(return_value=expected)),
    ):
        result, provider = await router.generate_structured("s", "u", LLMProfileExtraction)
    assert provider == "openrouter:openrouter/free"
    assert result is expected


# ---------------------------------------------------------------------
# main.py: hot-lead count only includes leads that were really delivered
# ---------------------------------------------------------------------


def _record() -> DatasetLeadRecord:
    return DatasetLeadRecord(
        recordType="hot_lead", source="reddit", subSource="r/x", authorHandle="u/a",
        competitorMentioned="Wave", painCategory="pricing", painSummary="x",
        postUrl="https://www.reddit.com/r/x/comments/1/a/",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "charge_result, expect_pushed, expected",
    [
        ((True, False), True, (True, True)),    # delivered, under limit
        ((False, False), False, (False, True)),  # billing refused: NOT delivered
        ((True, True), True, (True, False)),     # delivered but limit reached: stop afterwards
    ],
)
async def test_save_qualified_hot_lead_reports_real_delivery(charge_result, expect_pushed, expected):
    try:
        import src.main as main_mod
    except Exception as exc:  # e.g. `apify` cannot import on this interpreter
        pytest.skip(f"src.main not importable here: {type(exc).__name__}")

    with (
        patch.object(main_mod, "safe_charge_event", AsyncMock(return_value=charge_result)),
        patch.object(main_mod.Actor, "push_data", AsyncMock()) as push,
    ):
        outcome = await main_mod.save_qualified_hot_lead(_record())

    assert outcome == expected
    assert push.await_count == (1 if expect_pushed else 0)


# ---------------------------------------------------------------------
# Phase 1: Apify OpenRouter proxy tier
# ---------------------------------------------------------------------


def _fake_openai(content: str):
    create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, create


def test_proxy_enabled_only_on_platform_with_token(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    monkeypatch.delenv("APIFY_IS_AT_HOME", raising=False)
    assert LLMRouter().use_apify_proxy is False  # local: proxy rejects non-run tokens

    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    assert LLMRouter().use_apify_proxy is True

    monkeypatch.delenv("APIFY_TOKEN")
    assert LLMRouter().use_apify_proxy is False  # no token, no proxy


@pytest.mark.asyncio
async def test_proxy_is_tried_first_and_sends_claude_safe_request(monkeypatch):
    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    expected = _extraction()
    client, create = _fake_openai("```json\n" + expected.model_dump_json() + "\n```")
    router = LLMRouter(gemini_api_key="g", anthropic_api_key="a")
    gemini = AsyncMock()
    with (
        patch("src.llm_router.AsyncOpenAI", return_value=client) as ctor,
        patch.object(router, "_call_gemini", gemini),
    ):
        result, provider = await router.generate_structured("sys", "user", LLMProfileExtraction, temperature=0.3)

    assert provider == "apify-openrouter:anthropic/claude-haiku-5.5"
    assert result == expected
    gemini.assert_not_awaited()
    assert ctor.call_args.kwargs["base_url"] == "https://openrouter.apify.actor/api/v1"
    assert ctor.call_args.kwargs["api_key"] == "run-token"
    req = create.await_args.kwargs
    assert req["model"] == "anthropic/claude-haiku-5.5"
    assert "temperature" not in req  # Claude rejects non-default sampling
    assert "response_format" not in req
    assert req["max_tokens"] == 16000
    # Keeps Haiku's thinking small so it can't eat the output cap (seen live: finish_reason=length).
    assert req["extra_body"] == {"reasoning": {"effort": "low"}}


@pytest.mark.asyncio
async def test_proxy_failure_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    expected = _extraction()
    router = LLMRouter(gemini_api_key="g", apify_proxy_model="z-ai/glm-5.3-flash")
    with (
        patch.object(router, "_call_openai_compat", AsyncMock(side_effect=RuntimeError("402 credits"))),
        patch.object(router, "_call_gemini", AsyncMock(return_value=expected)),
    ):
        result, provider = await router.generate_structured("s", "u", LLMProfileExtraction)
    assert provider == "gemini:gemini-3.5-flash-lite"
    assert result is expected


@pytest.mark.asyncio
async def test_non_claude_proxy_model_keeps_temperature(monkeypatch):
    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    client, create = _fake_openai(_extraction().model_dump_json())
    router = LLMRouter(apify_proxy_model="deepseek/deepseek-v4.1-flash")
    with patch("src.llm_router.AsyncOpenAI", return_value=client):
        await router.generate_structured("s", "u", LLMProfileExtraction, temperature=0.1)
    assert create.await_args.kwargs["temperature"] == 0.1


# ---------------------------------------------------------------------
# Phase 2: revenue-ready lead intelligence
# ---------------------------------------------------------------------

from src.drafter import _to_dataset_record, recommended_action  # noqa: E402
from src.schemas import ScoredComplaint  # noqa: E402

QUOTE_TEXT = "FreshBooks bumped me to a pricier tier at 50 clients. Looking for an alternative with flat pricing asap."


def _eval(intent: str, quote: str = "", reasons=None, solv: int = 45, urg: int = 27) -> LLMComplaintEvaluation:
    return LLMComplaintEvaluation(
        idx=0,
        is_genuine_pain_or_seeking_alternative=True,
        competitor_mentioned="FreshBooks",
        pain_category="pricing_jump",
        pain_summary="Tier jump at 50 clients.",
        can_client_resolve=True,
        matched_capability_indices=[1],
        solvability_score=solv,
        urgency_score=urg,
        intent_signal=intent,
        intent_evidence_quote=quote,
        opportunity_reasons=reasons or [],
    )


async def _score_one(ev: LLMComplaintEvaluation, text: str = QUOTE_TEXT):
    item = _make_complaint("q1", text, age_hours=5)
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock(return_value=(LLMBatchEvaluationResponse(evaluations=[ev]), "gemini:t"))  # type: ignore[method-assign]
    return await score_complaints_batched([item], _sample_profile(), _sample_input(), llm_router=router, now=NOW)


@pytest.mark.asyncio
async def test_act_now_requires_explicit_intent():
    strong = await _score_one(_eval("explicit_alternative_search"))
    assert len(strong.hot_leads) == 1 and not strong.hot_leads[0].downgraded_for_intent

    weak = await _score_one(_eval("pricing_pain"))  # same high score, but nobody asked to switch
    assert weak.hot_leads == []
    assert len(weak.warm_signals) == 1 and weak.warm_signals[0].downgraded_for_intent


@pytest.mark.asyncio
async def test_intent_label_is_normalised_and_unknown_becomes_none():
    r = await _score_one(_eval("Asking For Recommendation"))
    assert r.hot_leads[0].intent_signal == "asking_for_recommendation"
    r = await _score_one(_eval("wants to buy stuff"))
    assert r.warm_signals[0].intent_signal == "none"


@pytest.mark.asyncio
async def test_quote_kept_only_if_it_really_appears_in_the_post():
    real = await _score_one(_eval("explicit_alternative_search", quote="Looking for an alternative with flat pricing"))
    assert real.hot_leads[0].intent_evidence_quote == "Looking for an alternative with flat pricing"
    fake = await _score_one(_eval("explicit_alternative_search", quote="I will pay anything for a better tool"))
    assert fake.hot_leads[0].intent_evidence_quote == ""


@pytest.mark.asyncio
async def test_opportunity_reasons_are_capped_at_three():
    r = await _score_one(_eval("explicit_alternative_search", reasons=["a reason", "b reason", "c reason", "d reason"]))
    assert r.hot_leads[0].opportunity_reasons == ["a reason", "b reason", "c reason"]


def _scored(intent="explicit_alternative_search", source="reddit", url="https://www.reddit.com/r/x/comments/1/a/", **kw):
    item = _make_complaint("s1", QUOTE_TEXT, url=url, source=source)
    base = dict(
        complaint=item, lead_tier="hot_lead", is_genuine_pain=True, can_client_resolve=True,
        competitor_mentioned="FreshBooks", pain_category="pricing", pain_summary="Tier jump.",
        matched_capabilities=["Flat $15/mo pricing with unlimited clients"], intent_signal=intent,
    )
    base.update(kw)
    return ScoredComplaint(**base)


def test_recommended_action_varies_by_tier_platform_and_urgency():
    assert "Reply publicly today" in recommended_action(_scored(), "hot_lead")
    assert "within a few hours" in recommended_action(_scored(intent="blocked_now"), "hot_lead")
    review = _scored(source="google_serp", url="https://www.g2.com/products/freshbooks/reviews/1")
    assert "Don't cold-contact the reviewer" in recommended_action(review, "hot_lead")
    assert "Don't cold-contact the reviewer" in recommended_action(review, "warm_signal")  # no thread to follow
    assert "no buying intent yet" in recommended_action(_scored(downgraded_for_intent=True), "warm_signal")
    partial = recommended_action(_scored(intent="blocked_now"), "warm_signal")  # urgent but weak fit
    assert "only partly fits" in partial and "not urgent" not in partial
    assert "not urgent" in recommended_action(_scored(intent="general_frustration"), "warm_signal")
    gap = _scored(rejection_or_gap_reason="Missing feature: payroll")
    assert "roadmap signal (Missing feature: payroll)" in recommended_action(gap, "unresolved_market_gap")


def test_dataset_record_carries_lead_intelligence_fields():
    rec = _to_dataset_record(
        _scored(intent_evidence_quote="Looking for an alternative", opportunity_reasons=["Asks to switch"]),
        "hot_lead",
        capability_sources=["https://invoicespark.io", "Founder-supplied product description"],
    )
    assert rec.priority == "Act now"
    assert rec.intentLabel == "Looking for an alternative"
    assert rec.intentEvidenceQuote == "Looking for an alternative"
    assert rec.opportunityReasons == ["Asks to switch"]
    assert rec.recommendedAction.startswith("Reply publicly")
    assert rec.capabilitySources[0] == "https://invoicespark.io"
    gap = _to_dataset_record(_scored(matched_capabilities=[]), "unresolved_market_gap", capability_sources=["x"])
    assert gap.priority == "Product insight" and gap.capabilitySources == []


def test_batch_sizes_fit_the_proxy_output_cap(monkeypatch):
    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    proxy = LLMRouter()
    assert proxy.output_token_cap == 2048
    # ~1000 tokens reserved for thinking (measured live: 690-900), so batches stay small.
    assert proxy.items_per_call(230, 15) == 4   # Stage 4 evaluations
    assert proxy.items_per_call(600, 5) == 1    # Stage 5 drafts
    monkeypatch.delenv("APIFY_IS_AT_HOME")
    assert LLMRouter().items_per_call(230, 15) == 15  # direct providers keep big batches


@pytest.mark.asyncio
async def test_stage4_uses_proxy_sized_batches(monkeypatch):
    monkeypatch.setenv("APIFY_IS_AT_HOME", "1")
    monkeypatch.setenv("APIFY_TOKEN", "run-token")
    items = [_make_complaint(f"b{i}", f"Wave invoicing problem number {i} with clients", age_hours=5) for i in range(15)]
    router = LLMRouter()
    sizes: list[int] = []

    async def fake(system_prompt, user_prompt, response_model, temperature=0.2):
        n = user_prompt.count("[POST idx=")
        sizes.append(n)
        return LLMBatchEvaluationResponse(evaluations=[]), "apify-openrouter:t"

    router.generate_structured = fake  # type: ignore[method-assign]
    await score_complaints_batched(items, _sample_profile(), _sample_input(), llm_router=router, now=NOW)
    assert sizes == [4, 4, 4, 3]


# ---------------------------------------------------------------------
# Phase 3: Competitor Pain Radar
# ---------------------------------------------------------------------

from src.radar import build_competitor_radar, ground_radar  # noqa: E402
from src.report import _build_radar_html  # noqa: E402
from src.schemas import (  # noqa: E402
    CompetitorRadar,
    LLMRadarPositioning,
    LLMRadarResponse,
    LLMRadarUnmetNeed,
    LLMRadarWeakness,
    Stage4Result,
)


def _pain(cid: str, competitor: str, theme: str, solvable: bool = True, tier: str = "warm_signal") -> ScoredComplaint:
    item = _make_complaint(cid, f"{competitor} problem {cid}", url=f"https://www.reddit.com/r/x/comments/{cid}/p/")
    return ScoredComplaint(
        complaint=item, lead_tier=tier, is_genuine_pain=True, can_client_resolve=solvable,
        competitor_mentioned=competitor, pain_category=theme, pain_summary=f"{competitor} {theme}",
        matched_capabilities=["Flat $15/mo pricing with unlimited clients"] if solvable else [],
    )


def _stage4_with(n: int) -> Stage4Result:
    pool = [_pain(f"p{i}", "FreshBooks" if i % 2 == 0 else "Wave", "pricing_jump") for i in range(n)]
    noise = _pain("noise", "Wave", "off_topic", solvable=False, tier="discarded")
    noise.is_genuine_pain = False
    return Stage4Result(warm_signals=pool, discarded=[noise])


def test_radar_grounding_drops_invented_evidence_and_bad_capabilities():
    stage4 = _stage4_with(4)
    pool = stage4.warm_signals
    llm = LLMRadarResponse(
        headline="FreshBooks pricing jumps drive switching.",
        weaknesses=[
            LLMRadarWeakness(competitor="FreshBooks", weakness="Tier jumps at client limits", evidence_ids=["E1", "E3", "E99"]),
            LLMRadarWeakness(competitor="Wave", weakness="Made-up weakness", evidence_ids=["E42"]),  # no real evidence
        ],
        positioning=[
            LLMRadarPositioning(customer_complaint="Per-client tiers", your_angle="Flat pricing, unlimited clients",
                                capability_index=1, evidence_ids=["e1", "2", "E1"]),
            LLMRadarPositioning(customer_complaint="x", your_angle="y", capability_index=99, evidence_ids=["E1"]),
        ],
        unmet_needs=[LLMRadarUnmetNeed(need="Payroll filing", evidence_ids=[])],
    )
    weaknesses, positioning, unmet, dropped = ground_radar(llm, pool, _sample_profile())

    assert [w.weakness for w in weaknesses] == ["Tier jumps at client limits"]
    assert weaknesses[0].mention_count == 2  # E1 + E3; E99 ignored; count from evidence, not the LLM
    assert weaknesses[0].evidence_urls == [pool[0].complaint.post_url, pool[2].complaint.post_url]
    assert len(positioning) == 1
    assert positioning[0].capability == "Flat $15/mo pricing with unlimited clients"
    assert positioning[0].mention_count == 2  # "e1", "2" normalised; duplicate E1 not double-counted
    assert unmet == []
    assert dropped == 3  # invented weakness, bad capability index, unmet need without evidence


@pytest.mark.asyncio
async def test_radar_counts_only_genuine_pain_and_skips_llm_when_evidence_is_thin():
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock()  # type: ignore[method-assign]
    radar = await build_competitor_radar(_stage4_with(2), _sample_profile(), _sample_input(), llm_router=router)

    router.generate_structured.assert_not_awaited()
    assert radar.evidence_items_considered == 2  # the off-topic noise item is excluded
    assert radar.competitor_counts == {"FreshBooks": 1, "Wave": 1}
    assert radar.pain_theme_counts == {"Pricing Jump": 2}
    assert "at least 3" in radar.note and radar.weaknesses == []


@pytest.mark.asyncio
async def test_radar_llm_failure_still_returns_counts():
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock(side_effect=RuntimeError("all providers down"))  # type: ignore[method-assign]
    radar = await build_competitor_radar(_stage4_with(4), _sample_profile(), _sample_input(), llm_router=router)
    assert radar.evidence_items_considered == 4 and radar.competitor_counts
    assert "counts only" in radar.note and radar.headline == ""


@pytest.mark.asyncio
async def test_radar_end_to_end_with_mocked_llm_and_prompt_lists_evidence_ids():
    seen: dict = {}

    async def fake(system_prompt, user_prompt, response_model, temperature=0.2):
        seen["prompt"] = user_prompt
        return (
            LLMRadarResponse(
                headline="Pricing tiers are the top churn driver.",
                weaknesses=[LLMRadarWeakness(competitor="FreshBooks", weakness="Tier jumps", evidence_ids=["E1", "E3"])],
            ),
            "gemini:t",
        )

    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = fake  # type: ignore[method-assign]
    radar = await build_competitor_radar(_stage4_with(4), _sample_profile(), _sample_input(), llm_router=router)
    assert "E1 | competitor: FreshBooks" in seen["prompt"] and "E4 |" in seen["prompt"]
    assert radar.headline == "Pricing tiers are the top churn driver."
    assert radar.weaknesses[0].mention_count == 2 and radar.provider == "gemini:t"


def test_radar_report_section_renders_and_escapes():
    radar = CompetitorRadar(
        evidence_items_considered=5,
        headline="<script>alert(1)</script> pricing",
        weaknesses=[{"competitor": "Wave", "weakness": "Slow support", "mention_count": 2,
                     "evidence_urls": ["https://www.reddit.com/r/a/1", "javascript:alert(1)"]}],
        positioning=[{"customer_complaint": "Per-client tiers", "your_angle": "Flat pricing",
                      "capability": "Flat $15/mo pricing", "mention_count": 1, "evidence_urls": ["https://x.io/p"]}],
    )
    html = _build_radar_html(radar)
    assert "Competitor Pain Radar" in html and "Positioning opportunities" in html and "Slow support" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    empty = _build_radar_html(CompetitorRadar(note="Only 1 relevant complaint(s) in this run."))
    assert "Only 1 relevant complaint" in empty
    assert _build_radar_html(None) == ""


def test_radar_counts_each_competitor_in_a_multi_name_field():
    from src.radar import _counts

    multi = _pain("m1", "SerpApi, Serper / Bright Data and Oxylabs", "data_quality")
    single = _pain("m2", "Bright Data", "pricing_jump")
    none = _pain("m3", "None", "pricing_jump")
    competitors, _, _ = _counts([multi, single, none])
    assert competitors == {"Bright Data": 2, "SerpApi": 1, "Serper": 1, "Oxylabs": 1, "Category-level": 1}


@pytest.mark.asyncio
async def test_radar_retries_once_in_compact_mode_after_a_cut_off_reply():
    prompts: list[str] = []

    async def fake(system_prompt, user_prompt, response_model, temperature=0.2):
        prompts.append(system_prompt)
        if len(prompts) == 1:
            raise ValueError("output failed validation (finish_reason=length)")
        return LLMRadarResponse(
            headline="Short.", weaknesses=[LLMRadarWeakness(competitor="Wave", weakness="Slow", evidence_ids=["E2"])]
        ), "apify-openrouter:t"

    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = fake  # type: ignore[method-assign]
    radar = await build_competitor_radar(_stage4_with(4), _sample_profile(), _sample_input(), llm_router=router)
    assert len(prompts) == 2 and "COMPACT MODE" in prompts[1] and "COMPACT MODE" not in prompts[0]
    assert radar.weaknesses[0].weakness == "Slow" and radar.note == ""


# ---------------------------------------------------------------------
# Phase 4: URL-only input + revenue calculator
# ---------------------------------------------------------------------

import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from src.profiler import build_product_profile, derive_product_name, extract_site_name  # noqa: E402
from src.report import _build_revenue_calculator_html  # noqa: E402
from src.revenue import compute_revenue_scenario, value_per_customer  # noqa: E402
from src.schemas import ActorInput, CrawlDiagnostics  # noqa: E402


def test_actor_input_needs_only_a_url():
    a = ActorInput(productUrl="getdataforme.com")
    assert a.productUrl == "https://getdataforme.com"
    assert a.productName == "" and a.productDescription == ""
    assert a.dealValueUsd is None and a.assumedConversionRatePercent == 10
    assert ActorInput(productUrl="x.io", productName=None, productDescription="  ").productDescription == ""


def test_input_schema_requires_only_the_url_and_has_calculator_fields():
    schema = _json.loads(_Path(".actor/input_schema.json").read_text(encoding="utf-8"))
    assert schema["required"] == ["productUrl"]
    assert list(schema["properties"])[0] == "productUrl"
    for field in ("dealValueUsd", "billingPeriod", "contractMonths", "assumedConversionRatePercent"):
        assert field in schema["properties"] and schema["properties"][field]["description"]


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<meta property="og:site_name" content="GetDataForMe"><title>Other | Thing</title>', "GetDataForMe"),
        ("<title>GetDataForMe | Managed Web Scraping</title>", "GetDataForMe"),
        ("<title>Plausible – Simple Analytics</title>", "Plausible"),
        ("<title>Acme: invoices made easy</title>", "Acme"),
        ("<p>no title</p>", None),
    ],
)
def test_site_name_extraction(html, expected):
    assert extract_site_name(f"<html><head>{html}</head><body></body></html>") == expected


def test_product_name_falls_back_to_domain_stem():
    assert derive_product_name(None, "getdataforme.com") == "Getdataforme"
    assert derive_product_name("  Acme  ", "acme.io") == "Acme"


@pytest.mark.asyncio
async def test_profile_from_url_only_names_product_from_website():
    inp = ActorInput(productUrl="https://invoicespark.io")
    diag = CrawlDiagnostics(crawl_method="httpx_direct", urls_fetched=["https://invoicespark.io"], site_name="InvoiceSpark")
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock(return_value=(_extraction(), "gemini:t"))  # type: ignore[method-assign]
    with patch("src.profiler.fetch_product_website_text", AsyncMock(return_value=("Flat $15/mo invoicing.", diag))):
        profile = await build_product_profile(inp, llm_router=router)

    assert inp.productName == "InvoiceSpark" and profile.product_name == "InvoiceSpark"
    prompt = router.generate_structured.await_args.kwargs["user_prompt"]
    assert "Product Name: InvoiceSpark" in prompt and "[Not supplied" in prompt
    assert "InvoiceSpark" in profile.exclude_terms  # own brand still excluded from results


@pytest.mark.asyncio
async def test_profile_fails_clearly_when_site_unreadable_and_no_description():
    inp = ActorInput(productUrl="https://blocked.example.com")
    diag = CrawlDiagnostics(crawl_method="description_only_fallback", warning="HTTP 403")
    router = LLMRouter(gemini_api_key="x")
    router.generate_structured = AsyncMock()  # type: ignore[method-assign]
    with patch("src.profiler.fetch_product_website_text", AsyncMock(return_value=("", diag))):
        with pytest.raises(ValueError, match="productDescription"):
            await build_product_profile(inp, llm_router=router)
    router.generate_structured.assert_not_awaited()


def test_value_per_customer_by_billing_period():
    assert value_per_customer(20, "monthly", 12) == 240
    assert value_per_customer(1200, "yearly", 24) == 2400
    assert value_per_customer(500, "one_time", 12) == 500


def test_revenue_scenario_uses_expected_fractions_and_needs_a_deal_value():
    inp = ActorInput(productUrl="x.io", dealValueUsd=20, billingPeriod="monthly", contractMonths=12)
    s = compute_revenue_scenario(act_now_count=20, monitor_count=5, actor_input=inp)
    assert s.expected_customers == 2.0 and s.illustrative_revenue_usd == 480.0  # the plan's own example
    s2 = compute_revenue_scenario(2, 0, inp)
    assert s2.expected_customers == 0.2 and s2.illustrative_revenue_usd == 48.0  # never rounded up
    assert "Not a forecast" in s2.label
    assert compute_revenue_scenario(0, 3, inp).illustrative_revenue_usd == 0
    assert compute_revenue_scenario(5, 0, ActorInput(productUrl="x.io")) is None  # no invented price


def test_calculator_html_is_prefilled_labelled_and_safe():
    inp = ActorInput(productUrl="x.io", dealValueUsd=2500, billingPeriod="yearly", contractMonths=24,
                     assumedConversionRatePercent=15)
    html = _build_revenue_calculator_html(2, 3, inp)
    assert 'id="rc-deal" type="number" min="0" step="1" value="2500"' in html
    assert 'el("rc-billing").value = "yearly";' in html
    assert 'value="15"' in html and "var ACT = 2, MON = 3;" in html
    assert "Illustrative scenario, not a forecast." in html and "function calc()" in html
    assert "__" not in html.replace("__REPORT_META__", "")  # every placeholder filled
    blank = _build_revenue_calculator_html(1, 0, ActorInput(productUrl="x.io"))
    assert 'value=""' in blank and "Enter a deal value" in blank
