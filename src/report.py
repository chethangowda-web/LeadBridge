"""LeadBridge report (REPORT.html): a self-contained, XSS-safe opportunity brief.

Design: "The Bridge". The page tells one story, from noise to real customers to revenue:
  - Cover: a one-sentence answer to "what should I do today?" and a funnel from posts scanned
    down to the people ready to buy.
  - Act now: each lead as a conversation (their words, why it matters, your proof, the next
    step, tabbed reply drafts with one-click copy).
  - Competitor Pain Radar, an interactive revenue calculator, Monitor and Product insight lists,
    and a proof section that shows how every claim was verified.
Single file, no external fonts, scripts or images. Print styles make REPORT.pdf look designed.
Every dynamic value goes through _esc() / _safe_href().
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timezone

from src.schemas import (
    ActorInput,
    CollectorTelemetry,
    CompetitorRadar,
    DatasetLeadRecord,
    ProductProfile,
    Stage3Result,
    Stage4Result,
    Stage5Result,
)


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


def _safe_href(url: str) -> str:
    cleaned = (url or "").strip()
    if cleaned.lower().startswith(("http://", "https://")):
        return html.escape(cleaned, quote=True)
    return "#"


SOURCE_LABELS = {
    "reddit": "Reddit",
    "hacker_news": "Hacker News",
    "bluesky": "Bluesky",
    "google_serp": "Web search",
    "g2": "G2 reviews",
}
PLACEHOLDER_AUTHORS = {"public_web", "anonymous", "redditor", "unknown", ""}
NO_COMPETITOR = {"none", "category-level", "category level", ""}

# ----------------------------------------------------------------------------------------------
# Icons (inline SVG, stroke = currentColor)
# ----------------------------------------------------------------------------------------------


def _svg(body: str, extra: str = "") -> str:
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"{extra}>{body}</svg>'
    )


ICON_BRIDGE = _svg('<path d="M2 18h20"/><path d="M4 18c0-6 3.6-10 8-10s8 4 8 10"/><path d="M8 18v-5M12 18V8M16 18v-5"/>')
ICON_CHECK = _svg('<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16 9.5"/>')
ICON_ARROW = _svg('<path d="M5 12h14M13 6l6 6-6 6"/>')
ICON_EXT = _svg('<path d="M7 17 17 7M9 7h8v8"/>')
ICON_QUOTE = _svg('<path d="M9 7H5v6h4v4l4-5V7zM19 7h-4v6h4v4l4-5V7z" fill="currentColor" stroke="none" transform="translate(-2 0)"/>')
ICON_BOLT = _svg('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>')
ICON_BULB = _svg('<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5.9 1.1.9 1.7V16h5.4v-.5c0-.6.3-1.2.9-1.7A6 6 0 0 0 12 3z"/>')
ICON_SHIELD = _svg('<path d="M12 3 5 6v5c0 4.5 2.9 8.2 7 10 4.1-1.8 7-5.5 7-10V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>')
ICON_PDF = _svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9.5 14.5 12 17l2.5-2.5"/>')
ICON_GLOBE = _svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>')
ICON_OK = _svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>')
ICON_PARTIAL = _svg('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>')
ICON_SKIP = _svg('<path d="M6 12h12"/>')
ICON_FAIL = _svg('<path d="m7 7 10 10M17 7 7 17"/>')

# ----------------------------------------------------------------------------------------------
# Small building blocks
# ----------------------------------------------------------------------------------------------


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _comp_label(name: str) -> str:
    """Readable competitor label; long multi-name lists become 'A, B +N'."""
    cleaned = (name or "").strip()
    if cleaned.lower() in NO_COMPETITOR:
        return "Category-wide pain"
    parts = [part.strip() for part in re.split(r"[;,/]", cleaned) if part.strip()]
    if len(parts) > 2:
        return f"{parts[0]}, {parts[1]} +{len(parts) - 2}"
    return ", ".join(parts) or cleaned


def _source_label(source: str, sub_source: str) -> str:
    base = SOURCE_LABELS.get(source, source.replace("_", " ").title())
    sub = (sub_source or "").strip()
    if sub and sub.lower() not in {base.lower(), source.lower(), "google_serp"}:
        return f"{base} · {sub}"
    return base


def _score_ring(score: int) -> str:
    score = max(0, min(100, _int(score)))
    return (
        f'<div class="ring" style="--p:{score}" role="img" aria-label="Score {score} out of 100">'
        f'<div class="ring-in"><b>{score}</b><small>/100</small></div></div>'
    )


def _meter(label: str, value: int, maximum: int) -> str:
    value = max(0, min(maximum, _int(value)))
    pct = round(value / maximum * 100) if maximum else 0
    return (
        '<div class="meter"><div class="meter-top">'
        f"<span>{_esc(label)}</span><b>{value}/{maximum}</b></div>"
        f'<div class="meter-track"><div class="meter-fill" style="width:{pct}%"></div></div></div>'
    )


def _evidence_links(urls: list[str]) -> str:
    return " ".join(
        f'<a class="ev-link" href="{_safe_href(u)}" target="_blank" rel="noopener noreferrer">[{n}]</a>'
        for n, u in enumerate(urls, start=1)
    )


def _section_head(eyebrow: str, title: str, sub: str = "", head_id: str = "") -> str:
    id_attr = f' id="{head_id}"' if head_id else ""
    sub_html = f'<p class="sec-sub">{sub}</p>' if sub else ""
    return f'<p class="sec-eyebrow">{_esc(eyebrow)}</p><h2 class="sec-title"{id_attr}>{_esc(title)}</h2>{sub_html}'


# ----------------------------------------------------------------------------------------------
# Cover: headline, funnel, stat tiles, source health
# ----------------------------------------------------------------------------------------------


def _headline(act: int, monitor: int) -> str:
    if act > 0:
        who = "person is" if act == 1 else "people are"
        return f"<em>{act} {who}</em> ready to switch. Reach out today."
    if monitor > 0:
        return f"Nobody is ready to buy yet, but <em>{monitor}</em> {'is' if monitor == 1 else 'are'} worth watching."
    return "No buying signals in this window. Here is what customers are saying."


def _funnel_html(raw_total: int, kept: int, pain: int, act: int) -> str:
    stages = [
        ("Posts scanned", raw_total, "Public posts found about your competitors"),
        ("Passed the free filter", kept, "Duplicates, spam, your own brand and old posts removed at no cost"),
        ("Real customer pain", pain, "A real person describing their own problem"),
        ("Ready to buy", act, "Clear buying intent and a product match"),
    ]
    base = max(raw_total, kept, pain, act, 1)
    items = []
    for label, value, hint in stages:
        value = max(0, _int(value))
        width = max(5, round(value / base * 100))
        items.append(
            f'<li title="{_esc(hint)}"><span class="funnel-label">{_esc(label)}</span>'
            f'<span class="funnel-val">{value}</span>'
            f'<div class="funnel-track"><div class="funnel-bar" style="width:{width}%"></div></div></li>'
        )
    return '<ol class="funnel">' + "".join(items) + "</ol>"


def _stat_tiles(act: int, monitor: int, insights: int, noise: int) -> str:
    tiles = [
        ("is-hero", act, "Act now", "Ready to engage today"),
        ("", monitor, "Monitor", "Relevant, not buying yet"),
        ("", insights, "Product insights", "Pain you can't solve yet"),
        ("", noise, "Noise removed", "Spam, promos and off-topic"),
    ]
    return "".join(
        f'<div class="stat {cls}"><div class="stat-num">{_int(num)}</div>'
        f'<div class="stat-label">{_esc(label)}</div><div class="stat-hint">{_esc(hint)}</div></div>'
        for cls, num, label, hint in tiles
    )


def _source_chips(telemetry: list[CollectorTelemetry]) -> str:
    icons = {"ok": ICON_OK, "partial": ICON_PARTIAL, "skipped": ICON_SKIP, "failed": ICON_FAIL}
    words = {"ok": "ok", "partial": "partial", "skipped": "skipped", "failed": "failed"}
    chips = []
    for t in telemetry:
        status = t.status if t.status in icons else "skipped"
        chips.append(
            f'<span class="src {status}">{icons[status]}'
            f"<span>{_esc(SOURCE_LABELS.get(t.source, t.source))}</span>"
            f'<b>{_int(t.items_collected)}</b><span class="src-status">{words[status]}</span></span>'
        )
    return "".join(chips)


# ----------------------------------------------------------------------------------------------
# Act now: lead cards
# ----------------------------------------------------------------------------------------------


def _quote_block(lead: DatasetLeadRecord) -> str:
    author = (lead.authorHandle or "").strip()
    meta = [_source_label(lead.source, lead.subSource)]
    if author.lower() not in PLACEHOLDER_AUTHORS:
        meta.append(author)
    if lead.publishedAt:
        meta.append(f"Posted {lead.publishedAt[:10]}")
    meta_html = "".join(f"<span>{_esc(m)}</span>" for m in meta)
    link = (
        f'<a href="{_safe_href(lead.postUrl)}" target="_blank" rel="noopener noreferrer">Open the post {ICON_EXT}</a>'
    )
    if lead.intentEvidenceQuote:
        body = f"<blockquote>&ldquo;{_esc(lead.intentEvidenceQuote)}&rdquo;</blockquote>"
        label = "In their own words"
    else:
        body = f'<blockquote class="plain">{_esc(lead.postTitle or lead.painSummary)}</blockquote>'
        label = "The post"
    title_line = (
        f'<div class="quote-title">{_esc(lead.postTitle)}</div>' if lead.intentEvidenceQuote and lead.postTitle else ""
    )
    return (
        f'<p class="label">{label}</p><div class="quote"><span class="quote-mark">{ICON_QUOTE}</span>'
        f"{body}{title_line}<div class=\"quote-src\">{meta_html}{link}</div></div>"
    )


def _drafts_block(lead: DatasetLeadRecord, idx: int) -> str:
    def panel(key: str, label: str, text: str, hint: str) -> str:
        words = len((text or "").split())
        return (
            f'<div class="draft-panel" data-panel="{key}" role="tabpanel">'
            f'<p class="draft-label">{_esc(label)}</p>'
            f'<div class="bubble"><pre id="draft-{key}-{idx}">{_esc(text)}</pre></div>'
            f'<div class="draft-foot"><span class="muted">{words} words &middot; {_esc(hint)}</span>'
            f'<button class="btn btn-primary copy-btn" type="button" onclick="copyText(\'draft-{key}-{idx}\', this)">'
            f"Copy Draft {key.upper()}</button></div></div>"
        )

    claims = (
        f'<p class="claims">{ICON_SHIELD}<span><b>Why this reply is safe to send:</b> {_esc(lead.claimEvidenceSummary)}</span></p>'
        if lead.claimEvidenceSummary
        else ""
    )
    rule = (
        f'<details class="rule"><summary>Community rules for this platform</summary>'
        f'<p class="muted">{_esc(lead.platformSafetyRule)}</p></details>'
        if lead.platformSafetyRule
        else ""
    )
    return (
        '<section class="drafts" data-drafts>'
        '<div class="seg" role="tablist" aria-label="Reply drafts">'
        '<button class="seg-btn is-on" type="button" role="tab" data-draft="a" aria-selected="true">Draft A &middot; Public reply</button>'
        '<button class="seg-btn" type="button" role="tab" data-draft="b" aria-selected="false">Draft B &middot; Follow-up</button>'
        "</div>"
        + panel("a", "Draft A · Public reply", lead.draftPublicReply, "helpful first, disclose who you are")
        + panel("b", "Draft B · Follow-up", lead.draftDirectPitch, "only if they reply or ask")
        + claims
        + rule
        + "</section>"
    )


def _build_hot_lead_cards_html(hot_leads: list[DatasetLeadRecord]) -> str:
    if not hot_leads:
        return (
            '<div class="empty"><b>No opportunity was ready to act on in this window.</b>'
            "<p>Check Monitor below, or widen the lookback window and add known competitors to find more.</p></div>"
        )

    cards: list[str] = []
    for idx, lead in enumerate(hot_leads, start=1):
        comp = _comp_label(lead.competitorMentioned)
        pain = (lead.painCategory or "").replace("_", " ").title()
        why = "".join(f"<li>{ICON_CHECK}<span>{_esc(r)}</span></li>" for r in lead.opportunityReasons)
        why_block = f'<p class="label spaced">Why this is an opportunity</p><ul class="why">{why}</ul>' if why else ""
        caps = "".join(f"<span>{_esc(c)}</span>" for c in lead.matchedCapabilities)
        source_links = ", ".join(
            f'<a href="{_safe_href(s)}" target="_blank" rel="noopener noreferrer">{_esc(s)}</a>'
            if s.startswith("http")
            else _esc(s)
            for s in lead.capabilitySources
        )
        sources_line = f'<div class="sources-line">Capabilities taken from: {source_links}</div>' if source_links else ""

        cards.append(
            f"""
            <article class="lead" id="lead-{idx}">
              <header class="lead-head">
                {_score_ring(lead.winabilityScore)}
                <div class="lead-title">
                  <div class="chips">
                    <span class="chip chip-act">{ICON_BOLT}{_esc(lead.priority or "Act now")}</span>
                    <span class="chip chip-intent">{_esc(lead.intentLabel)}</span>
                    <span class="chip">#{idx}</span>
                  </div>
                  <h3>{_esc(comp)} <span class="dash">&mdash;</span> {_esc(pain)}</h3>
                  <p class="lead-pain">{_esc(lead.painSummary)}</p>
                </div>
                <a class="btn btn-ghost" href="{_safe_href(lead.postUrl)}" target="_blank" rel="noopener noreferrer">Open post {ICON_EXT}</a>
              </header>

              <div class="lead-grid">
                <div>
                  {_quote_block(lead)}
                  {why_block}
                </div>
                <div>
                  <p class="label">Your proof</p>
                  <div class="proof">{caps}</div>
                  {sources_line}
                  <div class="meters">
                    {_meter("Product fit", lead.solvabilityScore, 50)}
                    {_meter("Buying urgency", lead.urgencyScore, 30)}
                    {_meter("Freshness", lead.recencyScore, 20)}
                  </div>
                  <div class="action">{ICON_ARROW}<div><b>Next step</b><br>{_esc(lead.recommendedAction)}</div></div>
                </div>
              </div>
              {_drafts_block(lead, idx)}
            </article>
            """
        )
    return "\n".join(cards)


# ----------------------------------------------------------------------------------------------
# Competitor Pain Radar
# ----------------------------------------------------------------------------------------------


def _bar_rows(counts: dict[str, int], limit: int = 6) -> str:
    items = [(_comp_label(name) if (name or "").strip().lower() in NO_COMPETITOR else name, c) for name, c in counts.items()][:limit]
    if not items:
        return ""
    top = max((c for _, c in items), default=1) or 1
    return "".join(
        f'<div class="bar-row"><div class="bar-name" title="{_esc(name)}">{_esc(name)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{max(8, round(c / top * 100))}%"></div></div>'
        f'<div class="bar-val">{_int(c)}</div></div>'
        for name, c in items
    )


def _build_competitor_breakdown_html(
    hot_leads: list[DatasetLeadRecord],
    warm_signals: list[DatasetLeadRecord],
    market_gaps: list[DatasetLeadRecord],
) -> str:
    """Fallback chart (no radar): complaints per competitor, counted from the delivered records."""
    all_pain = hot_leads + warm_signals + market_gaps
    counts = Counter(i.competitorMentioned for i in all_pain if (i.competitorMentioned or "").lower() not in NO_COMPETITOR)
    if not counts:
        return ""
    return (
        '<section class="section"><p class="label">Complaints by competitor</p>'
        f'<div class="bars">{_bar_rows(dict(counts.most_common()))}</div></section>'
    )


def _build_radar_html(radar: CompetitorRadar | None) -> str:
    if radar is None:
        return ""
    head = _section_head(
        "Competitor Pain Radar",
        "Where your competitors are losing customers",
        head_id="h-radar",
    )
    if not (radar.weaknesses or radar.positioning or radar.unmet_needs):
        note = radar.note or "No recurring competitor pattern was strong enough to report in this run."
        return f'<section class="section" id="radar" aria-labelledby="h-radar">{head}<div class="radar"><p class="muted">{_esc(note)}</p></div></section>'

    pull = f'<p class="pull">{_esc(radar.headline)}</p>' if radar.headline else ""

    positioning = "".join(
        f"""
        <div class="pos">
          <div class="pos-side pos-them"><p class="label">They complain</p>{_esc(p.customer_complaint)}</div>
          <div class="pos-arrow">{ICON_ARROW}</div>
          <div class="pos-side pos-you"><p class="label">Your angle</p><b>{_esc(p.your_angle)}</b>
            <div class="muted pos-cap">Backed by: {_esc(p.capability)}</div>
            <div class="pos-ev">{_int(p.mention_count)} post(s) {_evidence_links(p.evidence_urls)}</div></div>
        </div>"""
        for p in radar.positioning
    )
    positioning_html = (
        f'<h3 class="sub-title">Positioning opportunities</h3><div class="pos-grid">{positioning}</div>' if positioning else ""
    )

    def _avatar(name: str) -> str:
        if (name or "").strip().lower() in NO_COMPETITOR:
            return ICON_GLOBE
        return _esc((name or "?").strip()[:1].upper())

    weak_rows = "".join(
        f"""
        <div class="weak">
          <div class="avatar" aria-hidden="true">{_avatar(w.competitor)}</div>
          <div><div class="weak-name">{_esc(_comp_label(w.competitor))}</div><div>{_esc(w.weakness)}</div>
            <div class="ev-row">{_evidence_links(w.evidence_urls)}</div></div>
          <div class="weak-count"><b>{_int(w.mention_count)}</b><span>post(s)</span></div>
        </div>"""
        for w in radar.weaknesses
    )
    weak_html = f'<h3 class="sub-title">Competitor weaknesses</h3><div class="weak-list">{weak_rows}</div>' if weak_rows else ""

    unmet = "".join(
        f'<li>{ICON_BULB}<span>{_esc(u.need)} <span class="muted">({_int(u.mention_count)} post(s) {_evidence_links(u.evidence_urls)})</span></span></li>'
        for u in radar.unmet_needs
    )
    unmet_html = f'<h3 class="sub-title">Unmet needs (roadmap signals)</h3><ul class="why bulbs">{unmet}</ul>' if unmet else ""

    bars = _bar_rows(radar.competitor_counts)
    themes = "".join(
        f'<span class="tag">{_esc(name)} <b>{_int(c)}</b></span>' for name, c in list(radar.pain_theme_counts.items())[:6]
    )
    side = ""
    if bars or themes:
        side = (
            '<div class="radar-cols"><div>'
            + (f'<p class="label">Who customers complain about</p><div class="bars">{bars}</div>' if bars else "")
            + "</div><div>"
            + (f'<p class="label">Top pain themes</p><div class="tagrow">{themes}</div>' if themes else "")
            + "</div></div>"
        )

    return f"""
    <section class="section" id="radar" aria-labelledby="h-radar">
      {head}
      <div class="radar">
        {pull}
        <p class="muted">Built from {_int(radar.evidence_items_considered)} relevant complaints in this run. Post counts are computed from the evidence, and every insight links to the posts behind it.</p>
        {side}
        {positioning_html}
        {weak_html}
        {unmet_html}
      </div>
    </section>
    """


# ----------------------------------------------------------------------------------------------
# Revenue calculator (plain template: JS braces need no escaping)
# ----------------------------------------------------------------------------------------------

REVENUE_CALCULATOR_TEMPLATE = """
<section class="section" id="revenue" aria-labelledby="h-revenue">
  <p class="sec-eyebrow">Revenue scenario</p>
  <h2 class="sec-title" id="h-revenue">What These Opportunities Could Be Worth</h2>
  <p class="sec-sub">Adjust the assumptions to match your business. Only <strong>Act now</strong> opportunities are counted unless you include Monitor items.</p>
  <div class="calc">
    <div class="calc-body">
      <div class="calc-controls">
        <div><label for="rc-deal">Deal value per customer (USD)</label><input id="rc-deal" type="number" min="0" step="1" value="__DEAL__" placeholder="e.g. 500"></div>
        <div><label for="rc-billing">Billing</label>
          <select id="rc-billing">
            <option value="monthly">Monthly</option><option value="yearly">Yearly</option><option value="one_time">One-time</option>
          </select></div>
        <div><label for="rc-months">Contract length (months)</label><input id="rc-months" type="number" min="1" max="120" step="1" value="__MONTHS__"></div>
        <div><label for="rc-conv">Assumed conversion: <span id="rc-conv-val">__CONV__</span>%</label><input id="rc-conv" type="range" min="1" max="100" step="1" value="__CONV__"></div>
        <div class="calc-check"><label><input id="rc-monitor" type="checkbox">Include Monitor items (__MONITOR__)</label></div>
      </div>
      <div class="calc-hero">
        <div class="calc-small">Illustrative revenue</div>
        <div class="calc-big" id="rc-revenue">-</div>
        <div class="calc-mini">
          <div><strong id="rc-base">__ACT_NOW__</strong><span>Opportunities counted</span></div>
          <div><strong id="rc-customers">-</strong><span>Expected customers</span></div>
          <div><strong id="rc-one">-</strong><span>Value of one conversion</span></div>
        </div>
        <p class="calc-assume" id="rc-assume"></p>
      </div>
    </div>
    <p class="calc-label"><strong>Illustrative scenario, not a forecast.</strong> Expected customers = opportunities &times; your assumed conversion rate, so it can be a fraction. These figures depend entirely on your assumptions and are not measured results.</p>
    <script>
      (function () {
        var ACT = __ACT_NOW__, MON = __MONITOR__;
        var el = function (id) { return document.getElementById(id); };
        el("rc-billing").value = "__BILLING__";
        var money = function (v) { return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 }); };
        function calc() {
          var deal = parseFloat(el("rc-deal").value) || 0;
          var months = Math.max(1, parseInt(el("rc-months").value, 10) || 1);
          var conv = parseInt(el("rc-conv").value, 10) || 0;
          var billing = el("rc-billing").value;
          el("rc-conv-val").textContent = conv;
          el("rc-months").disabled = billing === "one_time";
          var base = ACT + (el("rc-monitor").checked ? MON : 0);
          var perCustomer = billing === "monthly" ? deal * months : billing === "yearly" ? deal * months / 12 : deal;
          var expected = base * conv / 100;
          el("rc-base").textContent = base;
          el("rc-customers").textContent = expected.toFixed(2);
          if (deal <= 0) {
            el("rc-one").textContent = "Enter a deal value";
            el("rc-revenue").textContent = "-";
            el("rc-assume").textContent = "Enter your deal value to see the scenario.";
            return;
          }
          el("rc-one").textContent = money(perCustomer);
          el("rc-revenue").textContent = money(expected * perCustomer);
          el("rc-assume").textContent = "Based on " + money(deal) + (billing === "monthly" ? " per month for " + months + " months" : billing === "yearly" ? " per year over " + months + " months" : " one-time") + ", " + conv + "% conversion, " + base + " opportunity(ies).";
        }
        ["rc-deal", "rc-billing", "rc-months", "rc-conv", "rc-monitor"].forEach(function (id) {
          el(id).addEventListener("input", calc);
          el(id).addEventListener("change", calc);
        });
        calc();
      })();
    </script>
  </div>
</section>
"""


def _build_revenue_calculator_html(act_now: int, monitor: int, actor_input: ActorInput) -> str:
    """Interactive calculator; prefilled from the Actor input. Values are validated ints/literals."""
    return (
        REVENUE_CALCULATOR_TEMPLATE.replace("__ACT_NOW__", str(int(act_now)))
        .replace("__MONITOR__", str(int(monitor)))
        .replace("__DEAL__", str(int(actor_input.dealValueUsd)) if actor_input.dealValueUsd else "")
        .replace("__MONTHS__", str(int(actor_input.contractMonths)))
        .replace("__CONV__", str(int(actor_input.assumedConversionRatePercent)))
        .replace(
            "__BILLING__",
            actor_input.billingPeriod if actor_input.billingPeriod in ("monthly", "yearly", "one_time") else "monthly",
        )
    )


# ----------------------------------------------------------------------------------------------
# Monitor, Product insight, proof
# ----------------------------------------------------------------------------------------------


def _build_warm_table_html(warm_signals: list[DatasetLeadRecord]) -> str:
    if not warm_signals:
        return '<p class="muted">Nothing to monitor in this run.</p>'
    rows = []
    for item in warm_signals:
        comp = _comp_label(item.competitorMentioned)
        rows.append(
            f"""
            <div class="row">
              <div class="score-pill">{_int(item.winabilityScore)}</div>
              <div>
                <div class="row-title">{_esc(comp)} <span class="muted">&middot; {_esc(item.intentLabel)} &middot; {_esc(_source_label(item.source, item.subSource))}</span></div>
                <div>{_esc(item.painSummary)}</div>
                <div class="row-next muted">{_esc(item.recommendedAction)}</div>
              </div>
              <a class="btn btn-ghost" href="{_safe_href(item.postUrl)}" target="_blank" rel="noopener noreferrer">View {ICON_EXT}</a>
            </div>
            """
        )
    return f'<div class="rows">{"".join(rows)}</div>'


def _build_product_insight_html(market_gaps: list[DatasetLeadRecord]) -> str:
    if not market_gaps:
        return '<p class="muted">No unsolved competitor pain found in this run.</p>'
    rows = "".join(
        f"""
        <div class="row">
          <div class="score-pill gap">{ICON_BULB}</div>
          <div>
            <div class="row-title">{_esc(_comp_label(item.competitorMentioned))}</div>
            <div>{_esc(item.painSummary)}</div>
            <div class="row-next muted">Why you can't solve it yet: {_esc(item.gapOrRejectionReason or "No matching capability on your site")}</div>
          </div>
          <a class="btn btn-ghost" href="{_safe_href(item.postUrl)}" target="_blank" rel="noopener noreferrer">View {ICON_EXT}</a>
        </div>
        """
        for item in market_gaps
    )
    return f'<div class="rows">{rows}</div>'


def _build_rejected_proof_html(stage4: Stage4Result) -> str:
    sample = stage4.discarded[:8]
    if not sample:
        return '<p class="muted">No items were rejected by the Solvability Gate.</p>'
    rows = "".join(
        f"""
        <tr>
          <td>{_esc(item.complaint.source)} / {_esc(item.complaint.sub_source)}</td>
          <td>{_esc(item.complaint.title or item.complaint.text[:80])}</td>
          <td>{_esc(item.rejection_or_gap_reason or "Judged off-topic by the scorer (no reason returned)")}</td>
        </tr>
        """
        for item in sample
    )
    return (
        '<div class="table-wrap"><table class="table"><thead><tr><th>Source</th><th>Post</th><th>Why it was rejected</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


# ----------------------------------------------------------------------------------------------
# Styles and behaviour (plain strings: no brace escaping needed)
# ----------------------------------------------------------------------------------------------

STYLE = """
:root{--ink:#0E1020;--ink2:#171B36;--paper:#F4F1EA;--line:#E7E2D7;--text:#16152B;--muted:#5E5B6E;
--violet:#5B3DF5;--violet-d:#4328D6;--violet-soft:#EEEAFF;--violet-mid:#B9ADFF;
--signal:#C7F548;--signal-soft:#F0FAD4;--signal-ink:#2F6B0A;--amber:#9A4A06;--coral:#B42318;
--shadow:0 1px 2px rgba(14,16,32,.05),0 10px 28px rgba(14,16,32,.07);
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Inter,Helvetica,Arial,sans-serif;
--serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:72px}
body{margin:0;background:var(--paper);color:var(--text);font:15px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--violet-d)}
:focus-visible{outline:3px solid var(--violet);outline-offset:2px;border-radius:8px}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
.muted{color:var(--muted)}
b,strong{font-weight:700}

.topnav{position:sticky;top:0;z-index:50;background:rgba(244,241,234,.88);-webkit-backdrop-filter:saturate(1.4) blur(10px);backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
.topnav .wrap{display:flex;align-items:center;gap:16px;height:58px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:-.02em;color:var(--ink);font-size:17px}
.brand-mark{width:32px;height:32px;border-radius:10px;background:var(--ink);color:var(--signal);display:grid;place-items:center}
.brand-mark svg{width:20px;height:20px}
.navlinks{display:flex;gap:4px;margin-left:auto;overflow-x:auto;scrollbar-width:none}
.navlinks a{padding:6px 12px;border-radius:999px;color:var(--muted);text-decoration:none;font-size:13px;font-weight:700;white-space:nowrap}
.navlinks a:hover{color:var(--ink);background:rgba(14,16,32,.06)}
.navlinks a.is-on{background:var(--ink);color:#fff}

.btn{display:inline-flex;align-items:center;gap:7px;border:0;border-radius:12px;padding:9px 14px;font:700 13px var(--sans);cursor:pointer;text-decoration:none;transition:transform .12s,background .12s;white-space:nowrap}
.btn:active{transform:translateY(1px)}
.btn svg{width:15px;height:15px}
.btn-primary{background:var(--violet);color:#fff}
.btn-primary:hover{background:var(--violet-d)}
.btn-ghost{background:var(--violet-soft);color:var(--violet-d)}
.btn-ghost:hover{background:#E1DAFF}
.btn-dark{background:var(--ink);color:#fff}

.hero{position:relative;overflow:hidden;color:#fff;padding:60px 0 104px;
background:radial-gradient(900px 440px at 88% -14%,rgba(91,61,245,.62),transparent 62%),radial-gradient(700px 360px at -6% 108%,rgba(199,245,72,.15),transparent 58%),var(--ink)}
.hero::before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse at 70% 15%,#000 0,transparent 72%);mask-image:radial-gradient(ellipse at 70% 15%,#000 0,transparent 72%)}
.hero-inner{position:relative;display:grid;grid-template-columns:1.12fr .88fr;gap:48px;align-items:center}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:800;color:var(--violet-mid);margin:0 0 16px}
.hero-title{font:600 clamp(34px,5.2vw,56px)/1.06 var(--serif);letter-spacing:-.025em;margin:0 0 18px}
.hero-title em{font-style:normal;color:var(--signal)}
.hero-sub{color:#CFCBE8;font-size:17px;max-width:54ch;margin:0 0 24px}
.hero-meta{display:flex;flex-wrap:wrap;gap:8px}
.pill-dark{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);color:#E6E4F7;padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700}

.funnel-card{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);border-radius:24px;padding:24px;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.funnel-title{font-size:12px;letter-spacing:.13em;text-transform:uppercase;color:#BDB9DD;margin:0 0 16px;font-weight:800}
.funnel{list-style:none;margin:0;padding:0;display:grid;gap:16px}
.funnel li{display:grid;grid-template-columns:1fr auto;gap:6px 12px;align-items:end}
.funnel-label{font-size:13.5px;font-weight:700}
.funnel-val{font:600 24px/1 var(--serif)}
.funnel-track{grid-column:1/-1;height:12px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden}
.funnel-bar{height:100%;border-radius:99px;background:linear-gradient(90deg,#7E67FF,#B9ADFF);transform-origin:left;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}
.funnel li:last-child .funnel-bar{background:linear-gradient(90deg,#A5E32A,var(--signal))}
.funnel li:last-child .funnel-val{color:var(--signal)}
@keyframes grow{from{transform:scaleX(0)}}

.stats{position:relative;z-index:2;margin-top:-58px;display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.stat{background:#fff;border:1px solid var(--line);border-radius:20px;padding:20px 22px;box-shadow:var(--shadow)}
.stat-num{font:600 42px/1 var(--serif);letter-spacing:-.02em}
.stat-label{margin-top:8px;font-size:12px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;color:var(--ink)}
.stat-hint{font-size:12.5px;color:var(--muted);margin-top:2px}
.stat.is-hero{background:var(--ink);border-color:var(--ink);color:#fff}
.stat.is-hero .stat-num{color:var(--signal)}
.stat.is-hero .stat-label{color:#fff}
.stat.is-hero .stat-hint{color:#BDB9DD}

.sources{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px;align-items:center}
.sources .label{margin:0 6px 0 0}
.src{display:inline-flex;gap:7px;align-items:center;background:#fff;border:1px solid var(--line);padding:5px 12px;border-radius:999px;font-size:12.5px;font-weight:600}
.src svg{width:14px;height:14px}
.src.ok svg{color:var(--signal-ink)}.src.partial svg{color:var(--amber)}.src.skipped svg{color:var(--muted)}.src.failed svg{color:var(--coral)}
.src-status{color:var(--muted);font-weight:600}

.section{margin-top:60px}
.sec-eyebrow{font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--violet-d);margin:0 0 6px}
.sec-title{font:600 clamp(26px,3.2vw,36px)/1.15 var(--serif);margin:0 0 8px;letter-spacing:-.015em}
.sec-sub{color:var(--muted);margin:0 0 24px;max-width:72ch}
.sub-title{font:700 13px var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:30px 0 12px}
.label{font-size:11.5px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 9px}
.label.spaced{margin-top:20px}
.empty{background:#fff;border:1px dashed #CFC8B8;border-radius:20px;padding:28px;text-align:center}
.empty p{margin:6px 0 0;color:var(--muted)}

.lead{position:relative;background:#fff;border:1px solid var(--line);border-radius:26px;box-shadow:var(--shadow);padding:28px 28px 24px 32px;margin-bottom:24px;overflow:hidden}
.lead::before{content:"";position:absolute;left:0;top:0;bottom:0;width:6px;background:linear-gradient(180deg,var(--signal),var(--violet))}
.lead-head{display:grid;grid-template-columns:auto 1fr auto;gap:22px;align-items:start}
.ring{--p:0;width:96px;height:96px;border-radius:50%;background:conic-gradient(var(--violet) calc(var(--p)*1%),#E9E5F7 0);display:grid;place-items:center;position:relative}
.ring::before{content:"";position:absolute;inset:10px;background:#fff;border-radius:50%}
.ring-in{position:relative;text-align:center;line-height:1}
.ring-in b{font:600 32px var(--serif);display:block}
.ring-in small{font-size:11px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:9px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;padding:4px 11px;border-radius:999px;background:#F0EDE4;color:#3A3852}
.chip svg{width:13px;height:13px}
.chip-act{background:var(--ink);color:var(--signal)}
.chip-intent{background:var(--violet-soft);color:var(--violet-d)}
.lead-title h3{font:600 24px/1.2 var(--serif);margin:0 0 6px;letter-spacing:-.01em}
.dash{color:var(--violet-mid)}
.lead-pain{margin:0;color:var(--muted)}
.lead-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:26px;margin-top:24px}
.quote{position:relative;background:#FAF8F3;border:1px solid var(--line);border-radius:18px;padding:18px 18px 14px 56px}
.quote-mark{position:absolute;left:14px;top:12px;width:32px;height:32px;color:var(--violet-mid)}
.quote-mark svg{width:32px;height:32px}
.quote blockquote{margin:0;font:italic 500 19px/1.5 var(--serif)}
.quote blockquote.plain{font-style:normal;font-size:16px}
.quote-title{margin-top:8px;font-size:12.5px;color:var(--muted)}
.quote-src{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:12px;font-size:12.5px;color:var(--muted);align-items:center}
.quote-src a{font-weight:700;text-decoration:none;display:inline-flex;gap:4px;align-items:center}
.quote-src a svg{width:13px;height:13px}
.why{list-style:none;margin:0;padding:0;display:grid;gap:9px}
.why li{display:flex;gap:10px;align-items:flex-start}
.why svg{width:19px;height:19px;flex:none;margin-top:3px;color:var(--signal-ink)}
.bulbs svg{color:var(--violet)}
.proof{display:flex;flex-wrap:wrap;gap:7px}
.proof span{background:var(--violet-soft);color:var(--violet-d);border-radius:10px;padding:5px 11px;font-size:12.5px;font-weight:600}
.sources-line{font-size:12px;color:var(--muted);margin-top:9px;overflow-wrap:anywhere}
.meters{display:grid;gap:10px;margin-top:18px}
.meter-top{display:flex;justify-content:space-between;font-size:12px;font-weight:700;color:#3A3852}
.meter-track{height:7px;border-radius:99px;background:#ECE8F7;overflow:hidden;margin-top:4px}
.meter-fill{height:100%;border-radius:99px;background:var(--violet)}
.action{display:flex;gap:12px;align-items:flex-start;margin-top:18px;background:var(--signal-soft);border:1px solid #DDEFA6;border-radius:16px;padding:14px 16px;font-size:14px}
.action svg{width:20px;height:20px;flex:none;color:var(--signal-ink);margin-top:2px}

.drafts{margin-top:24px;border-top:1px dashed #D9D3C4;padding-top:20px}
.seg{display:inline-flex;background:#F0EDE4;border-radius:13px;padding:4px;gap:4px;margin-bottom:14px}
.seg-btn{border:0;background:transparent;padding:8px 15px;border-radius:10px;font:700 13px var(--sans);color:var(--muted);cursor:pointer}
.seg-btn.is-on{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(14,16,32,.14)}
.draft-panel+.draft-panel{margin-top:14px}
.draft-label{display:none;font:800 11.5px var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
.bubble{background:linear-gradient(180deg,#F7F4FF,#F1EEFF);border:1px solid #E1DAFF;border-radius:20px 20px 20px 6px;padding:18px 20px}
.bubble pre{margin:0;white-space:pre-wrap;font:15px/1.65 var(--sans);color:var(--text);overflow-wrap:anywhere}
.draft-foot{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap;font-size:12.5px}
.claims{display:flex;gap:9px;font-size:12.5px;color:var(--muted);margin:16px 0 0}
.claims svg{width:18px;height:18px;flex:none;color:var(--signal-ink)}
.rule{margin-top:12px;font-size:13px}
.rule summary{cursor:pointer;font-weight:700;color:var(--muted)}

.radar{background:#fff;border:1px solid var(--line);border-radius:26px;box-shadow:var(--shadow);padding:30px}
.pull{font:600 clamp(20px,2.5vw,28px)/1.3 var(--serif);letter-spacing:-.01em;margin:0 0 8px;padding-left:18px;border-left:6px solid var(--signal)}
.radar-cols{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-top:24px}
.bars{display:grid;gap:12px}
.bar-row{display:grid;grid-template-columns:130px 1fr 30px;gap:12px;align-items:center;font-size:13px}
.bar-name{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{height:9px;border-radius:99px;background:#ECE8F7;overflow:hidden}
.bar-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#7E67FF,var(--violet));transform-origin:left;animation:grow .8s both}
.bar-val{font-weight:800;text-align:right}
.tagrow{display:flex;flex-wrap:wrap;gap:8px}
.tag{background:#F3F0E8;border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:600}
.tag b{color:var(--violet-d);margin-left:4px}
.pos-grid{display:grid;gap:14px}
.pos{display:grid;grid-template-columns:1fr 38px 1.2fr;gap:12px;align-items:stretch}
.pos-side{border-radius:18px;padding:16px 18px;font-size:14px}
.pos-them{background:#FBF4F1;border:1px solid #F1DDD4;display:flex;flex-direction:column;justify-content:center}
.pos-you{background:var(--signal-soft);border:1px solid #DDEFA6}
.pos-arrow{display:grid;place-items:center;color:var(--violet)}
.pos-arrow svg{width:24px;height:24px}
.pos-cap{margin-top:6px;font-size:12.5px}
.pos-ev{margin-top:8px;font-size:12px;color:var(--muted)}
.weak-list{display:grid}
.weak{display:grid;grid-template-columns:40px 1fr auto;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)}
.weak:last-child{border-bottom:0}
.avatar{width:40px;height:40px;border-radius:13px;background:var(--violet-soft);color:var(--violet-d);display:grid;place-items:center;font-weight:800;font-size:16px}
.avatar svg{width:20px;height:20px}
.weak-name{font-weight:800;margin-bottom:2px}
.weak-count{text-align:right;line-height:1.1}
.weak-count b{display:block;font:600 24px var(--serif)}
.weak-count span{font-size:11.5px;color:var(--muted)}
.ev-row{margin-top:4px}
.ev-link{display:inline-block;min-width:24px;text-align:center;font-size:11.5px;font-weight:800;border-radius:7px;padding:1px 6px;background:var(--violet-soft);text-decoration:none;margin-right:3px}

.calc{position:relative;overflow:hidden;background:var(--ink);color:#fff;border-radius:28px;padding:30px;
background-image:radial-gradient(600px 280px at 100% 0,rgba(91,61,245,.5),transparent 65%),radial-gradient(500px 260px at 0 110%,rgba(199,245,72,.12),transparent 60%)}
.calc-body{position:relative;display:grid;grid-template-columns:1.05fr .95fr;gap:30px}
.calc-controls{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-content:start}
.calc label{display:block;font-size:12px;font-weight:700;color:#BDB9DD;margin-bottom:6px}
.calc input[type=number],.calc select{width:100%;background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.22);color:#fff;border-radius:12px;padding:11px 13px;font:600 15px var(--sans)}
.calc input[type=number]::placeholder{color:#9893C2}
.calc select option{color:#111}
.calc input[type=range]{width:100%;accent-color:var(--signal)}
.calc-check{display:flex;align-items:flex-end}
.calc-check label{display:flex;gap:8px;align-items:center;margin:0;color:#E6E4F7;font-size:13px}
.calc-hero{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.16);border-radius:22px;padding:24px}
.calc-small{font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:800;color:#BDB9DD}
.calc-big{font:600 clamp(40px,5.2vw,60px)/1.05 var(--serif);color:var(--signal);letter-spacing:-.02em;margin:6px 0 4px;overflow-wrap:anywhere}
.calc-mini{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:18px}
.calc-mini div{border-top:1px solid rgba(255,255,255,.16);padding-top:10px}
.calc-mini strong{display:block;font:600 19px/1.25 var(--serif)}
.calc-mini span{font-size:11.5px;color:#BDB9DD}
.calc-assume{margin:14px 0 0;font-size:12.5px;color:#CFCBE8}
.calc-label{position:relative;margin:20px 0 0;background:rgba(254,243,199,.12);border:1px solid rgba(254,243,199,.38);color:#FDE9A8;border-radius:14px;padding:11px 16px;font-size:12.5px}

.rows{display:grid;gap:10px}
.row{display:grid;grid-template-columns:60px 1fr auto;gap:16px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:18px;padding:14px 18px}
.score-pill{width:52px;height:52px;border-radius:16px;background:var(--violet-soft);color:var(--violet-d);display:grid;place-items:center;font:600 21px var(--serif)}
.score-pill.gap{background:var(--signal-soft);color:var(--signal-ink)}
.score-pill svg{width:24px;height:24px}
.row-title{font-weight:800}
.row-next{font-size:13px;margin-top:2px}

details.fold{background:#fff;border:1px solid var(--line);border-radius:18px;padding:2px 20px;margin-bottom:12px}
details.fold summary{cursor:pointer;font-weight:800;padding:15px 0}
details.fold[open] summary{border-bottom:1px solid var(--line);margin-bottom:14px}
.checks{list-style:none;margin:0 0 16px;padding:0;display:grid;gap:10px}
.checks li{display:flex;gap:10px}
.checks svg{width:19px;height:19px;flex:none;margin-top:3px;color:var(--signal-ink)}
.ledger{margin:0 0 16px;padding-left:20px;color:var(--muted)}
.table-wrap{overflow-x:auto;margin-bottom:14px}
.table{width:100%;border-collapse:collapse;font-size:13px}
.table th,.table td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
.table th{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}

.foot{margin-top:64px;background:var(--ink);color:#BDB9DD;padding:34px 0 40px;font-size:12.5px}
.foot .brand{color:#fff;margin-bottom:10px}
.foot p{margin:0 0 8px;max-width:80ch}

@media (max-width:900px){
.hero-inner,.lead-grid,.radar-cols,.calc-body{grid-template-columns:1fr}
.stats{grid-template-columns:repeat(2,1fr)}
.lead-head{grid-template-columns:auto 1fr}.lead-head .btn{grid-column:1/-1;justify-self:start}
.pos{grid-template-columns:1fr}.pos-arrow{transform:rotate(90deg);padding:2px 0}
.row{grid-template-columns:52px 1fr}.row .btn{grid-column:1/-1;justify-self:start}
.navlinks{display:none}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important}}
@media print{
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#fff;font-size:12.5px}
.topnav,.copy-btn,.seg,.calc-controls,.calc-check{display:none!important}
.hero{padding:34px 0 76px}
.stats{margin-top:-46px}
.section{margin-top:36px}
.radar,.calc,.row,.pos,.stat,details.fold{break-inside:avoid;box-shadow:none}
.lead{break-inside:auto;box-shadow:none}
.lead-head,.lead-grid>div>*,.meters,.proof{break-inside:avoid}
.radar{break-inside:auto}
.weak,.bar-row{break-inside:avoid}
.draft-panel[hidden]{display:block!important}
.draft-label{display:block!important}
.funnel-bar,.bar-fill{animation:none}
.calc-body{grid-template-columns:1fr}
.sec-eyebrow,.sec-title,.sec-sub,.sub-title{break-after:avoid}
.draft-panel,.action,.quote{break-inside:avoid}
.lead{padding:20px 20px 18px 24px;margin-bottom:16px}
.lead-grid{margin-top:16px;gap:20px}
.ring{width:76px;height:76px}.ring::before{inset:8px}.ring-in b{font-size:25px}
.lead-title h3{font-size:20px}
.quote blockquote{font-size:16px}
.bubble{padding:14px 16px}.bubble pre{font-size:13px;line-height:1.55}
.drafts{margin-top:16px;padding-top:14px}
.stat-num{font-size:34px}.stat{padding:16px 18px}
.radar,.calc{padding:22px}
.pos-side{padding:12px 14px}
.hero-title{font-size:42px}
}
"""

SCRIPT = """
(function () {
  document.documentElement.classList.add("js");
  window.copyText = function (id, btn) {
    var el = document.getElementById(id);
    if (!el) return;
    var text = el.innerText || el.textContent || "";
    var done = function () {
      var original = btn.getAttribute("data-label") || btn.innerText;
      btn.setAttribute("data-label", original);
      btn.innerText = "Copied!";
      setTimeout(function () { btn.innerText = original; }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) {}
      document.body.removeChild(ta);
    }
  };
  document.querySelectorAll("[data-drafts]").forEach(function (box) {
    var btns = box.querySelectorAll(".seg-btn"), panels = box.querySelectorAll(".draft-panel");
    function show(key) {
      btns.forEach(function (b) {
        var on = b.dataset.draft === key;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      panels.forEach(function (p) { p.hidden = p.dataset.panel !== key; });
    }
    btns.forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.draft); }); });
    show("a");
  });
  var links = [].slice.call(document.querySelectorAll(".navlinks a"));
  if ("IntersectionObserver" in window && links.length) {
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        links.forEach(function (l) { l.classList.remove("is-on"); });
        if (map[e.target.id]) map[e.target.id].classList.add("is-on");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) { var s = document.getElementById(id); if (s) io.observe(s); });
  }
})();
"""

# ----------------------------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------------------------


def render_executive_report_html(
    profile: ProductProfile,
    actor_input: ActorInput,
    stage2_telemetry: list[CollectorTelemetry],
    stage3: Stage3Result,
    stage4: Stage4Result,
    stage5: Stage5Result,
    radar: CompetitorRadar | None = None,
) -> str:
    """Compile the complete single-file HTML opportunity brief."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    raw_total = _int(stage3.input_count)
    act = len(stage5.hot_lead_records)
    monitor = len(stage5.warm_signal_records)
    insights = len(stage5.market_gap_records)
    pain = sum(
        1
        for group in (stage4.hot_leads, stage4.warm_signals, stage4.low_priority, stage4.unresolved_market_gaps)
        for item in group
        if item.is_genuine_pain
    )
    noise = _int(stage3.dropped_count) + len(stage4.discarded)

    competitors = [c for c in profile.competitors[:3] if c.strip()]
    watched = ", ".join(_esc(c) for c in competitors) if competitors else "your competitors"
    sources_used = ", ".join(SOURCE_LABELS.get(s, s) for s in actor_input.sources)
    radar_html = _build_radar_html(radar) or _build_competitor_breakdown_html(
        stage5.hot_lead_records, stage5.warm_signal_records, stage5.market_gap_records
    )
    calculator_html = _build_revenue_calculator_html(act, monitor, actor_input)

    nav = [("overview", "Overview"), ("act-now", "Act now")]
    if radar is not None:
        nav.append(("radar", "Radar"))
    nav += [("revenue", "Revenue"), ("monitor", "Monitor"), ("insights", "Insights"), ("proof", "Proof")]
    nav_html = "".join(f'<a href="#{i}">{_esc(label)}</a>' for i, label in nav)

    ledger = "".join(f"<li>{_esc(cap)}</li>" for cap in profile.verified_capabilities)
    meta_json = json.dumps(
        {"product": profile.product_name, "actNow": act, "monitor": monitor, "generatedAt": generated_at}
    ).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>LeadBridge Brief &mdash; {_esc(profile.product_name)}</title>
  <style>{STYLE}</style>
</head>
<body>
  <nav class="topnav" aria-label="Report sections">
    <div class="wrap">
      <div class="brand"><span class="brand-mark">{ICON_BRIDGE}</span>LeadBridge</div>
      <div class="navlinks">{nav_html}</div>
      <button class="btn btn-dark" type="button" onclick="window.print()">{ICON_PDF}Save as PDF</button>
    </div>
  </nav>

  <header class="hero" id="overview">
    <div class="wrap hero-inner">
      <div>
        <p class="eyebrow">Opportunity brief &middot; {_esc(profile.product_name)} &middot; {_esc(generated_at)}</p>
        <h1 class="hero-title">{_headline(act, monitor)}</h1>
        <p class="hero-sub">We scanned {raw_total} public posts about {watched} and kept only the people your product can actually help.</p>
        <div class="hero-meta">
          <span class="pill-dark">Last {_int(actor_input.lookbackDays)} days</span>
          <span class="pill-dark">Act-now threshold {_int(actor_input.minWinabilityScore)}/100</span>
          <span class="pill-dark">{_esc(sources_used)}</span>
        </div>
      </div>
      <aside class="funnel-card" aria-label="From noise to customers">
        <h2 class="funnel-title">From noise to customers</h2>
        {_funnel_html(raw_total, _int(stage3.kept_count), pain, act)}
      </aside>
    </div>
  </header>

  <main class="wrap">
    <div class="stats">{_stat_tiles(act, monitor, insights, noise)}</div>
    <div class="sources"><span class="label">Sources</span>{_source_chips(stage2_telemetry)}</div>

    <section class="section" id="act-now" aria-labelledby="h-act">
      {_section_head("Act now", "Ready-to-engage opportunities", "People showing explicit buying or switching intent, where one of your proven capabilities matches their stated pain.", "h-act")}
      {_build_hot_lead_cards_html(stage5.hot_lead_records)}
    </section>

    {radar_html}

    {calculator_html}

    <section class="section" id="monitor" aria-labelledby="h-monitor">
      {_section_head("Monitor", "Relevant, but no clear buying intent yet", "Good fit, but nobody is asking to switch. Watch these threads and engage only if they ask for options.", "h-monitor")}
      {_build_warm_table_html(stage5.warm_signal_records)}
    </section>

    <section class="section" id="insights" aria-labelledby="h-insights">
      {_section_head("Product insight", "Pain you can't solve yet", "Real complaints your product doesn't cover today. Useful as roadmap signals.", "h-insights")}
      {_build_product_insight_html(stage5.market_gap_records)}
    </section>

    <section class="section" id="proof" aria-labelledby="h-proof">
      {_section_head("Proof", "How every claim was verified", "", "h-proof")}
      <details class="fold" open>
        <summary>The checks behind every lead</summary>
        <ul class="checks">
          <li>{ICON_CHECK}<span>A lead is <b>Act now</b> only when the person shows real buying intent, not just a high score.</span></li>
          <li>{ICON_CHECK}<span>Quotes are kept only if they appear <b>word for word</b> in the original post.</span></li>
          <li>{ICON_CHECK}<span>Replies may mention only capabilities found on <b>your own website</b>.</span></li>
          <li>{ICON_CHECK}<span>Radar insights must link to the posts behind them; anything without evidence is dropped.</span></li>
        </ul>
      </details>
      <details class="fold">
        <summary>Verified capabilities ledger ({len(profile.verified_capabilities)} facts)</summary>
        <ol class="ledger">{ledger}</ol>
      </details>
      <details class="fold">
        <summary>Posts we rejected ({len(stage4.discarded)}), none charged as leads</summary>
        {_build_rejected_proof_html(stage4)}
      </details>
    </section>
  </main>

  <footer class="foot">
    <div class="wrap">
      <div class="brand"><span class="brand-mark">{ICON_BRIDGE}</span>LeadBridge</div>
      <p><strong>Compliance &amp; privacy.</strong> LeadBridge never posts anything for you and never scrapes private or logged-in data. Review every draft before you send it, follow each community's self-promotion rules, and respect GDPR and local privacy law if you enrich a public handle later.</p>
      <p>Generated {_esc(generated_at)}.</p>
    </div>
  </footer>

  <script>window.__REPORT_META__ = {meta_json};</script>
  <script>{SCRIPT}</script>
</body>
</html>
"""
