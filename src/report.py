"""Stage 5C: Self-Contained HTML Executive Brief Generator (REPORT.html).

Compiles a single-file, zero-CDN, XSS-safe executive dashboard saved to Apify
Key-Value Store (REPORT.html) featuring:
  - Pipeline Funnel KPIs (Raw -> Clean -> Hot Leads -> Warm Signals -> Gaps)
  - Competitor Pain Breakdown horizontal bar chart
  - Hot Lead Action Cards with one-click Copy Draft A & Copy Draft B buttons
  - Platform Anti-Ban Guidance & Exact Verified Capability Evidence pills
  - Hard-Gate Noise Rejection Proof (demonstrating false-positive filtering)
  - Human-in-the-loop & GDPR privacy compliance footer
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone

from src.schemas import (
    CompetitorRadar,
    ActorInput,
    CollectorTelemetry,
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


def _build_competitor_breakdown_html(
    hot_leads: list[DatasetLeadRecord],
    warm_signals: list[DatasetLeadRecord],
    market_gaps: list[DatasetLeadRecord],
) -> str:
    all_pain = hot_leads + warm_signals + market_gaps
    if not all_pain:
        return '<p class="muted">No competitor complaints qualified in this lookback window.</p>'

    comp_counts = Counter(
        item.competitorMentioned for item in all_pain if item.competitorMentioned and item.competitorMentioned != "None"
    )
    cat_counts = Counter(item.painCategory.replace("_", " ").title() for item in all_pain)

    max_comp = max(comp_counts.values(), default=1)
    comp_bars = []
    for comp, count in comp_counts.most_common(6):
        pct = max(12, int((count / max_comp) * 100))
        comp_bars.append(
            f"""
            <div class="bar-row">
              <div class="bar-label">{_esc(comp)}</div>
              <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
              <div class="bar-val">{count}</div>
            </div>
            """
        )

    cat_pills = "".join(
        f'<span class="pill pill-amber">{_esc(cat)} <strong>({cnt})</strong></span>'
        for cat, cnt in cat_counts.most_common(6)
    )

    return f"""
    <div class="grid-2">
      <div>
        <h3 class="sub-h">Complaints by Competitor</h3>
        {''.join(comp_bars)}
      </div>
      <div>
        <h3 class="sub-h">Top Pain Themes</h3>
        <div class="pill-wrap">{cat_pills}</div>
      </div>
    </div>
    """


def _build_hot_lead_cards_html(hot_leads: list[DatasetLeadRecord]) -> str:
    if not hot_leads:
        return (
            '<div class="card empty-state">'
            "No opportunity was ready to act on in this lookback window. "
            "Check Monitor below or increase <code>lookbackDays</code>."
            "</div>"
        )

    cards: list[str] = []
    for idx, lead in enumerate(hot_leads, start=1):
        caps_html = "".join(
            f'<span class="pill pill-teal">&#10003; {_esc(c)}</span>'
            for c in lead.matchedCapabilities
        )
        draft_a_id = f"draft-a-{idx}"
        draft_b_id = f"draft-b-{idx}"
        reasons_html = "".join(f"<li>{_esc(r)}</li>" for r in lead.opportunityReasons)
        reasons_block = (
            f'<div class="section-mini"><div class="mini-label">Why this is an opportunity</div>'
            f'<ul class="reason-list">{reasons_html}</ul></div>'
            if reasons_html
            else ""
        )
        quote_block = (
            f'<blockquote class="evidence-quote">&ldquo;{_esc(lead.intentEvidenceQuote)}&rdquo; '
            f'<a href="{_safe_href(lead.postUrl)}" target="_blank" rel="noopener noreferrer">source &#8599;</a></blockquote>'
            if lead.intentEvidenceQuote
            else ""
        )
        source_links = ", ".join(
            f'<a href="{_safe_href(src)}" target="_blank" rel="noopener noreferrer">{_esc(src)}</a>'
            if src.startswith("http")
            else _esc(src)
            for src in lead.capabilitySources
        )
        sources_html = (
            f'<div class="muted sources-line">Capabilities taken from: {source_links}</div>'
            if lead.capabilitySources
            else ""
        )

        cards.append(
            f"""
            <article class="card lead-card">
              <div class="lead-header">
                <div class="lead-meta">
                  <span class="score-badge">{lead.winabilityScore}<span>/100</span></span>
                  <div>
                    <div class="pill-wrap" style="margin-bottom:4px;"><span class="pill pill-act">{_esc(lead.priority or 'Act now')}</span><span class="pill pill-purple">{_esc(lead.intentLabel)}</span></div>
                    <div class="lead-title">#{idx} &middot; {_esc(lead.competitorMentioned)} &mdash; {_esc(lead.painCategory.replace('_', ' ').title())}</div>
                    <div class="lead-sub">
                      <strong>{_esc(lead.source.upper())}</strong> ({_esc(lead.subSource)}) &middot;
                      {_esc(lead.authorHandle)} &middot;
                      Solvability {lead.solvabilityScore}/50 &middot; Urgency {lead.urgencyScore}/30 &middot; Recency {lead.recencyScore}/20
                    </div>
                  </div>
                </div>
                <a class="btn-link" href="{_safe_href(lead.postUrl)}" target="_blank" rel="noopener noreferrer">
                  Open Original Post &#8599;
                </a>
              </div>

              <p class="pain-summary"><strong>Customer Pain:</strong> {_esc(lead.painSummary)}</p>
              {quote_block}
              {reasons_block}

              <div class="section-mini">
                <div class="mini-label">Evidence-backed product match</div>
                <div class="pill-wrap">{caps_html}</div>
                {sources_html}
              </div>

              <div class="action-box">
                <strong>Recommended next action:</strong> {_esc(lead.recommendedAction)}
              </div>

              <div class="safety-box">
                <strong>Platform Anti-Ban Guidance:</strong> {_esc(lead.platformSafetyRule)}
              </div>

              <div class="drafts-grid">
                <div class="draft-box">
                  <div class="draft-top">
                    <span class="draft-label">Draft A &mdash; Value-First Public Reply</span>
                    <button class="copy-btn" type="button" onclick="copyText('{draft_a_id}', this)">Copy Draft A</button>
                  </div>
                  <pre id="{draft_a_id}" class="draft-text">{_esc(lead.draftPublicReply)}</pre>
                </div>

                <div class="draft-box">
                  <div class="draft-top">
                    <span class="draft-label">Draft B &mdash; Direct / DM Follow-Up</span>
                    <button class="copy-btn" type="button" onclick="copyText('{draft_b_id}', this)">Copy Draft B</button>
                  </div>
                  <pre id="{draft_b_id}" class="draft-text">{_esc(lead.draftDirectPitch)}</pre>
                </div>
              </div>
            </article>
            """
        )

    return "\n".join(cards)


def _build_warm_table_html(warm_signals: list[DatasetLeadRecord]) -> str:
    if not warm_signals:
        return '<p class="muted">Nothing to monitor in this run.</p>'

    rows = []
    for item in warm_signals:
        rows.append(
            f"""
            <tr>
              <td><span class="pill pill-purple">{item.winabilityScore}/100</span></td>
              <td>{_esc(item.source)} / {_esc(item.subSource)}</td>
              <td><strong>{_esc(item.competitorMentioned)}</strong></td>
              <td>{_esc(item.painSummary)}<br><span class="muted">{_esc(item.intentLabel)}</span></td>
              <td class="muted">{_esc(item.recommendedAction)}</td>
              <td><a href="{_safe_href(item.postUrl)}" target="_blank" rel="noopener noreferrer">View &#8599;</a></td>
            </tr>
            """
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Score</th><th>Source</th><th>Competitor</th><th>Pain / Signal</th><th>Next step</th><th>Link</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _build_product_insight_html(market_gaps: list[DatasetLeadRecord]) -> str:
    if not market_gaps:
        return '<p class="muted">No unsolved competitor pain found in this run.</p>'
    rows = "".join(
        f"""
            <tr>
              <td><strong>{_esc(item.competitorMentioned)}</strong></td>
              <td>{_esc(item.painSummary)}</td>
              <td class="muted">{_esc(item.gapOrRejectionReason)}</td>
              <td><a href="{_safe_href(item.postUrl)}" target="_blank" rel="noopener noreferrer">View &#8599;</a></td>
            </tr>
            """
        for item in market_gaps
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Competitor</th><th>Customer pain</th><th>Why you can't solve it yet</th><th>Link</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _build_rejected_proof_html(stage4: Stage4Result) -> str:
    sample = stage4.discarded[:8]
    if not sample:
        return '<p class="muted">No items were rejected by the Solvability Gate.</p>'

    rows = []
    for item in sample:
        rows.append(
            f"""
            <tr>
              <td>{_esc(item.complaint.source)} / {_esc(item.complaint.sub_source)}</td>
              <td>{_esc(item.complaint.title or item.complaint.text[:80])}</td>
              <td><span class="pill pill-coral">Rejected (0/100)</span></td>
              <td>{_esc(item.rejection_or_gap_reason or "Judged off-topic by the scorer (no reason returned)")}</td>
            </tr>
            """
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Source</th><th>Post</th><th>Gate Verdict</th><th>Why Rejected ($0 Lead Charge)</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _evidence_links(urls: list[str]) -> str:
    return " ".join(
        f'<a class="ev-link" href="{_safe_href(u)}" target="_blank" rel="noopener noreferrer">[{n}]</a>'
        for n, u in enumerate(urls, start=1)
    )


def _build_radar_html(radar: CompetitorRadar | None) -> str:
    if radar is None:
        return ""
    if not (radar.weaknesses or radar.positioning or radar.unmet_needs):
        note = radar.note or "No recurring competitor pattern was strong enough to report in this run."
        return (
            '<h2>Competitor Pain Radar</h2>'
            f'<section class="card"><p class="muted">{_esc(note)}</p></section>'
        )

    headline = (
        f'<p class="radar-headline">{_esc(radar.headline)}</p>' if radar.headline else ""
    )

    weakness_rows = "".join(
        f"""
            <tr>
              <td><strong>{_esc(w.competitor)}</strong></td>
              <td>{_esc(w.weakness)}</td>
              <td>{w.mention_count}</td>
              <td>{_evidence_links(w.evidence_urls)}</td>
            </tr>"""
        for w in radar.weaknesses
    )
    weakness_html = (
        '<h3 class="sub-h">Competitor weaknesses</h3>'
        '<div class="table-wrap"><table><thead><tr><th>Competitor</th><th>What customers dislike</th>'
        f'<th>Posts</th><th>Evidence</th></tr></thead><tbody>{weakness_rows}</tbody></table></div>'
        if weakness_rows
        else ""
    )

    positioning_cards = "".join(
        f"""
            <div class="pos-card">
              <div class="pos-complaint"><span class="mini-label">They complain</span>{_esc(p.customer_complaint)}</div>
              <div class="pos-arrow">&rarr;</div>
              <div class="pos-angle"><span class="mini-label">Your angle</span>{_esc(p.your_angle)}
                <div class="muted pos-cap">Backed by: {_esc(p.capability)}</div>
              </div>
              <div class="pos-ev">{p.mention_count} post(s) {_evidence_links(p.evidence_urls)}</div>
            </div>"""
        for p in radar.positioning
    )
    positioning_html = (
        f'<h3 class="sub-h">Positioning opportunities</h3><div class="pos-grid">{positioning_cards}</div>'
        if positioning_cards
        else ""
    )

    unmet_items = "".join(
        f"<li>{_esc(u.need)} <span class=\"muted\">({u.mention_count} post(s) {_evidence_links(u.evidence_urls)})</span></li>"
        for u in radar.unmet_needs
    )
    unmet_html = (
        f'<h3 class="sub-h">Unmet needs (roadmap signals)</h3><ul class="reason-list">{unmet_items}</ul>'
        if unmet_items
        else ""
    )

    return f"""
    <h2>Competitor Pain Radar</h2>
    <section class="card radar-card">
      {headline}
      <p class="muted">Built from {radar.evidence_items_considered} relevant complaints in this run. Post counts are computed from the evidence, and every insight links to the posts it is based on.</p>
      {positioning_html}
      {weakness_html}
      {unmet_html}
    </section>
    """


# Plain (non f-string) template so the JavaScript braces need no escaping.
REVENUE_CALCULATOR_TEMPLATE = """
<h2>What These Opportunities Could Be Worth</h2>
<section class="card calc-card">
  <style>
    .calc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 10px 0 16px; }
    .calc-grid label { font-size: 12px; color: var(--muted); font-weight: 600; display: block; }
    .calc-grid input, .calc-grid select { width: 100%; margin-top: 4px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
    .calc-out { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .calc-out div { background: var(--teal-soft); border-radius: 10px; padding: 12px; }
    .calc-out strong { display: block; font-size: 22px; color: var(--teal); }
    .calc-label { background: var(--amber-soft); color: #78350f; border-radius: 8px; padding: 8px 12px; font-size: 12px; margin-top: 12px; }
  </style>
  <p class="muted">Adjust the assumptions to match your business. Only <strong>Act now</strong> opportunities are counted unless you include Monitor items.</p>
  <div class="calc-grid">
    <div><label for="rc-deal">Deal value per customer (USD)</label><input id="rc-deal" type="number" min="0" step="1" value="__DEAL__" placeholder="e.g. 500"></div>
    <div><label for="rc-billing">Billing</label>
      <select id="rc-billing">
        <option value="monthly">Monthly</option><option value="yearly">Yearly</option><option value="one_time">One-time</option>
      </select></div>
    <div><label for="rc-months">Contract length (months)</label><input id="rc-months" type="number" min="1" max="120" step="1" value="__MONTHS__"></div>
    <div><label for="rc-conv">Assumed conversion: <span id="rc-conv-val">__CONV__</span>%</label><input id="rc-conv" type="range" min="1" max="100" step="1" value="__CONV__"></div>
    <div><label><input id="rc-monitor" type="checkbox" style="width:auto;margin-right:6px;">Include Monitor items (__MONITOR__)</label></div>
  </div>
  <div class="calc-out">
    <div><span class="muted">Opportunities counted</span><strong id="rc-base">__ACT_NOW__</strong></div>
    <div><span class="muted">Expected customers</span><strong id="rc-customers">-</strong></div>
    <div><span class="muted">Value of one conversion</span><strong id="rc-one">-</strong></div>
    <div><span class="muted">Illustrative revenue</span><strong id="rc-revenue">-</strong></div>
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
          return;
        }
        el("rc-one").textContent = money(perCustomer);
        el("rc-revenue").textContent = money(expected * perCustomer);
      }
      ["rc-deal", "rc-billing", "rc-months", "rc-conv", "rc-monitor"].forEach(function (id) {
        el(id).addEventListener("input", calc);
        el(id).addEventListener("change", calc);
      });
      calc();
    })();
  </script>
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
        .replace("__BILLING__", actor_input.billingPeriod if actor_input.billingPeriod in ("monthly", "yearly", "one_time") else "monthly")
    )


def render_executive_report_html(
    profile: ProductProfile,
    actor_input: ActorInput,
    stage2_telemetry: list[CollectorTelemetry],
    stage3: Stage3Result,
    stage4: Stage4Result,
    stage5: Stage5Result,
    radar: CompetitorRadar | None = None,
) -> str:
    """Compile the complete single-file HTML Executive Brief."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    raw_total = stage3.input_count

    source_badges = "".join(
        f'<span class="pill pill-neutral">{_esc(t.source)}: <strong>{t.items_collected}</strong> ({_esc(t.status)})</span>'
        for t in stage2_telemetry
    )

    ledger_items = "".join(
        f"<li>{_esc(cap)}</li>" for cap in profile.verified_capabilities
    )

    hot_cards_html = _build_hot_lead_cards_html(stage5.hot_lead_records)
    breakdown_html = _build_competitor_breakdown_html(
        stage5.hot_lead_records, stage5.warm_signal_records, stage5.market_gap_records
    )
    warm_html = _build_warm_table_html(stage5.warm_signal_records)
    insight_html = _build_product_insight_html(stage5.market_gap_records)
    radar_html = _build_radar_html(radar)
    calculator_html = _build_revenue_calculator_html(
        len(stage5.hot_lead_records), len(stage5.warm_signal_records), actor_input
    )
    rejected_html = _build_rejected_proof_html(stage4)

    # Safe JSON metadata for debugging / copy tooling
    meta_json = json.dumps(
        {
            "product": profile.product_name,
            "hotLeads": len(stage5.hot_lead_records),
            "warmSignals": len(stage5.warm_signal_records),
            "generatedAt": generated_at,
        }
    ).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>LeadBridge Brief &mdash; {_esc(profile.product_name)}</title>
  <style>
    :root {{
      --bg: #f8f7f4;
      --surface: #ffffff;
      --ink: #1e1b2e;
      --muted: #5c586e;
      --border: #e6e2ee;
      --purple: #3b1d6c;
      --teal: #0f766e;
      --teal-soft: #e6f6f4;
      --coral: #e05a47;
      --coral-soft: #fdecea;
      --amber: #b45309;
      --amber-soft: #fef3c7;
      --purple-soft: #efe9fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.55;
    }}
    .container {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 56px; }}
    .hero {{
      background: linear-gradient(135deg, var(--purple) 0%, #241046 100%);
      color: #fff;
      border-radius: 16px;
      padding: 28px 32px;
      margin-bottom: 24px;
    }}
    .hero-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }}
    .hero h1 {{ margin: 0 0 6px; font-size: 26px; letter-spacing: -0.02em; }}
    .hero p {{ margin: 0; color: rgba(255,255,255,0.82); font-size: 14px; }}
    .badge-live {{
      background: rgba(15, 118, 110, 0.25);
      border: 1px solid rgba(94, 234, 212, 0.45);
      color: #99f6e4;
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }}
    .kpi {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
    }}
    .kpi-num {{ font-size: 28px; font-weight: 800; color: var(--purple); line-height: 1.1; }}
    .kpi-num.teal {{ color: var(--teal); }}
    .kpi-num.coral {{ color: var(--coral); }}
    .kpi-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-top: 4px; }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 22px 24px;
      margin-bottom: 18px;
      box-shadow: 0 2px 8px rgba(30, 27, 46, 0.04);
    }}
    h2 {{ font-size: 19px; margin: 28px 0 12px; color: var(--purple); }}
    .sub-h {{ font-size: 14px; margin: 0 0 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 22px; }}
    .bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; }}
    .bar-label {{ width: 150px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .bar-track {{ flex: 1; height: 10px; background: #eeeaf5; border-radius: 999px; overflow: hidden; }}
    .bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--teal), var(--purple)); border-radius: 999px; }}
    .bar-val {{ width: 24px; text-align: right; font-weight: 700; color: var(--purple); }}
    .pill-wrap {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
    }}
    .pill-teal {{ background: var(--teal-soft); color: var(--teal); }}
    .pill-coral {{ background: var(--coral-soft); color: var(--coral); }}
    .pill-amber {{ background: var(--amber-soft); color: var(--amber); }}
    .pill-purple {{ background: var(--purple-soft); color: var(--purple); }}
    .pill-neutral {{ background: #f1eff6; color: var(--ink); }}
    .pill-act {{ background: var(--teal); color: #fff; }}
    .radar-headline {{ font-size: 16px; font-weight: 700; color: var(--purple); margin: 0 0 6px; }}
    .pos-grid {{ display: grid; gap: 12px; margin-bottom: 18px; }}
    .pos-card {{ display: grid; grid-template-columns: 1fr auto 1.3fr auto; gap: 12px; align-items: center; border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: #fbfafc; font-size: 13px; }}
    .pos-card .mini-label {{ display: block; }}
    .pos-arrow {{ font-size: 20px; color: var(--teal); }}
    .pos-cap {{ margin-top: 4px; font-size: 12px; }}
    .pos-ev {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
    .ev-link {{ color: var(--purple); font-weight: 600; text-decoration: none; }}
    @media (max-width: 720px) {{ .pos-card {{ grid-template-columns: 1fr; }} .pos-arrow {{ display: none; }} }}
    .evidence-quote {{ margin: 0 0 14px; padding: 8px 14px; border-left: 3px solid var(--purple); background: var(--purple-soft); font-size: 13px; font-style: italic; }}
    .evidence-quote a {{ font-style: normal; font-size: 12px; color: var(--purple); }}
    .reason-list {{ margin: 0; padding-left: 18px; font-size: 13px; }}
    .sources-line {{ margin-top: 6px; font-size: 12px; }}
    .action-box {{ background: var(--teal-soft); border-left: 4px solid var(--teal); padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 12px; }}
    .lead-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; margin-bottom: 12px; }}
    .lead-meta {{ display: flex; align-items: center; gap: 14px; }}
    .score-badge {{
      background: var(--purple);
      color: #fff;
      font-size: 20px;
      font-weight: 800;
      padding: 8px 12px;
      border-radius: 10px;
      min-width: 78px;
      text-align: center;
    }}
    .score-badge span {{ font-size: 12px; font-weight: 500; opacity: 0.78; }}
    .lead-title {{ font-size: 16px; font-weight: 700; color: var(--ink); }}
    .lead-sub {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
    .btn-link {{
      text-decoration: none;
      background: var(--purple-soft);
      color: var(--purple);
      font-weight: 600;
      font-size: 13px;
      padding: 7px 13px;
      border-radius: 8px;
    }}
    .btn-link:hover {{ background: #e3d7f7; }}
    .pain-summary {{ margin: 10px 0 14px; font-size: 14px; }}
    .section-mini {{ margin-bottom: 14px; }}
    .mini-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 700; margin-bottom: 6px; }}
    .safety-box {{
      background: var(--amber-soft);
      border-left: 4px solid var(--amber);
      padding: 10px 14px;
      border-radius: 6px;
      font-size: 13px;
      color: #78350f;
      margin-bottom: 16px;
    }}
    .drafts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }}
    .draft-box {{
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fbfafc;
      padding: 12px 14px;
    }}
    .draft-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 8px; }}
    .draft-label {{ font-size: 12px; font-weight: 700; color: var(--purple); }}
    .copy-btn {{
      background: var(--teal);
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 5px 11px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .copy-btn:hover {{ background: #0c5f59; }}
    .draft-text {{
      margin: 0;
      white-space: pre-wrap;
      font-family: inherit;
      font-size: 13px;
      color: var(--ink);
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
    th {{ background: #f4f1fa; color: var(--purple); font-size: 12px; text-transform: uppercase; }}
    details summary {{ cursor: pointer; font-weight: 700; color: var(--purple); }}
    .ledger-list {{ margin: 10px 0 0; padding-left: 20px; font-size: 13px; color: var(--muted); }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .footer {{
      margin-top: 32px;
      padding: 16px 20px;
      border-radius: 10px;
      background: #f1eff6;
      font-size: 12px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <div class="hero-top">
        <div>
          <h1>{_esc(profile.product_name)} &mdash; Competitor Churn &amp; Outreach Brief</h1>
          <p>{_esc(profile.product_category)} &middot; Lookback: {actor_input.lookbackDays} days &middot; Min Hot Score: {actor_input.minWinabilityScore}/100 &middot; Generated {generated_at}</p>
        </div>
        <span class="badge-live">LeadBridge &middot; Human-in-the-Loop Safe</span>
      </div>
    </header>

    <section class="kpi-grid">
      <div class="kpi"><div class="kpi-num">{raw_total}</div><div class="kpi-label">Harvested (Stage 2)</div></div>
      <div class="kpi"><div class="kpi-num">{stage3.dropped_count}</div><div class="kpi-label">Filtered Free (Stage 3)</div></div>
      <div class="kpi"><div class="kpi-num teal">{len(stage5.hot_lead_records)}</div><div class="kpi-label">Act Now</div></div>
      <div class="kpi"><div class="kpi-num">{len(stage5.warm_signal_records)}</div><div class="kpi-label">Monitor</div></div>
      <div class="kpi"><div class="kpi-num">{len(stage5.market_gap_records)}</div><div class="kpi-label">Product Insights</div></div>
      <div class="kpi"><div class="kpi-num">{len(stage4.low_priority)}</div><div class="kpi-label">Low Priority (Solvable, Weak)</div></div>
      <div class="kpi"><div class="kpi-num coral">{len(stage4.discarded)}</div><div class="kpi-label">Noise Rejected ($0)</div></div>
    </section>

    <section class="card">
      <div class="mini-label">Source Harvest Telemetry</div>
      <div class="pill-wrap" style="margin-bottom: 16px;">{source_badges}</div>
      {breakdown_html}
    </section>

    {radar_html}

    <h2>Act Now &mdash; Ready-to-Engage Opportunities ({len(stage5.hot_lead_records)})</h2>
    <p class="muted">People showing explicit buying or switching intent, where a listed capability matches their stated pain.</p>
    {hot_cards_html}

    {calculator_html}

    <h2>Monitor &mdash; Relevant, No Clear Buying Intent Yet ({len(stage5.warm_signal_records)})</h2>
    <section class="card">{warm_html}</section>

    <h2>Product Insight &mdash; Pain You Can't Solve Yet ({len(stage5.market_gap_records)})</h2>
    <section class="card">{insight_html}</section>

    <section class="card">
      <details>
        <summary>Solvability Hard-Gate Quality Proof ({len(stage4.discarded)} Rejected False Positives &mdash; $0 Lead Charge)</summary>
        <p class="muted" style="margin: 10px 0;">Every item below failed factual solvability against the Verified Capabilities Ledger and was discarded before drafting or billing.</p>
        {rejected_html}
      </details>
    </section>

    <section class="card">
      <details>
        <summary>Stage 1 Grounding Anchor: Verified Capabilities Ledger ({len(profile.verified_capabilities)} Facts)</summary>
        <ul class="ledger-list">{ledger_items}</ul>
      </details>
    </section>

    <footer class="footer">
      <strong>Compliance &amp; Privacy Notice:</strong> This Actor never auto-posts to Reddit, Hacker News, Bluesky, or review platforms and never scrapes private/logged-in personal data. Always review drafts before posting and ensure GDPR/local privacy compliance if you enrich any public handle later.
    </footer>
  </div>

  <script>
    window.__REPORT_META__ = {meta_json};
    function copyText(elementId, btn) {{
      const el = document.getElementById(elementId);
      if (!el) return;
      const text = el.innerText || el.textContent || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(() => {{
          const orig = btn.innerText;
          btn.innerText = "Copied!";
          setTimeout(() => {{ btn.innerText = orig; }}, 1600);
        }});
      }}
    }}
  </script>
</body>
</html>
"""