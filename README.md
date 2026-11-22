# LeadBridge: Find Customers Leaving Your Competitors

**For bootstrapped B2B SaaS and DevTool founders who don't have a sales team or a social-listening budget.**

Every day, people post on Reddit, Hacker News and review sites that they're fed up with a tool and are looking for something else. A few of them are your next customers. This Actor finds them for you.

Give it **your product URL**. It reads your website to learn what you sell, finds fresh complaints about your competitors, keeps only the people **your product can actually help** and who **show buying intent**, and gives you:

- **Act-now opportunities**: the person's own words, why it's an opportunity, the capability of yours that solves it, and the exact next step.
- **Two ready-to-review replies** per opportunity: a helpful public reply with a clear disclosure, and a short follow-up.
- **A Competitor Pain Radar**: what customers repeatedly dislike about each competitor, and how to position against it.
- **An illustrative revenue calculator**: what those opportunities could be worth under your own assumptions.

You pay for actionable opportunities, not for thousands of mentions.

## What a result looks like

From a real run for a managed web-scraping company (input: just the URL):

> **Act now · 77/100 · Asking for recommendations**
> *"Are there any other reliable providers you've used for similar services?"* ([r/scrapingtheweb](https://www.reddit.com/r/scrapingtheweb/comments/1y0hk3g/web_unlockers_vs_parallax_hyper_solutions_for/))
>
> **Why this is an opportunity**
> - Asks for reliable providers handling anti-bot blocking at scale
> - Managed anti-bot handling for major protection vendors fits the stated need
>
> **Recommended next action:** Reply publicly today with Draft A (helpful tip first, clear disclosure). Send Draft B only if they reply or ask for details.

> **Competitor Pain Radar:** "Teams seeking managed anti-bot scraping and relief from in-house maintenance are the clearest opening; lead with those capabilities."

## How it works

1. **Profile:** reads your homepage (plus your pricing or features page) and builds a list of capabilities it can prove from your own site. It picks your competitors and search queries.
2. **Harvest:** searches Google, Reddit, Hacker News and Bluesky for recent complaints and "looking for an alternative" posts.
3. **Filter (free):** removes duplicates, cross-posts, profile pages, spam, your own brand and old posts before any AI cost.
4. **Qualify:** an AI checks every post. Is it a real person with a real problem? Does one of your capabilities fix it? Are they ready to switch? Promotional posts, listicles and launch posts are rejected.
5. **Deliver:** drafts replies for Act-now opportunities, builds the Competitor Pain Radar, and writes a self-contained HTML report.

**What keeps it honest**
- A post only becomes **Act now** when the person shows real buying intent (asking for options, switching, weighing build vs. buy, or blocked right now).
- Quotes are kept only if they appear word for word in the post.
- Replies may only mention capabilities found on your own site.
- Radar insights must link to the posts they're based on; anything without evidence is dropped.
- It never posts anything for you. You review and send.

## Input

Only **Product Website URL** is required. Everything else is optional:

| Field | What it does |
|---|---|
| `productUrl` | Your homepage (required). |
| `productName`, `productDescription` | Optional. The name is read from your site; add a description only if your site can't be read. |
| `competitors` | Competitors you know you want to watch. Others are found automatically. |
| `sources` | Google, Reddit, Hacker News, Bluesky, and optionally G2 reviews. |
| `lookbackDays` | How far back to look (default 14; 30 finds more). |
| `minWinabilityScore` | Score needed for Act now (default 70). |
| `tone` | Voice of the drafted replies. |
| `dealValueUsd`, `billingPeriod`, `contractMonths`, `assumedConversionRatePercent` | Optional, for the revenue calculator. |
| `modelName` | AI model, run through Apify's OpenRouter proxy (default Claude Haiku 5.5). No separate AI key needed. |

```json
{ "productUrl": "https://your-product.com", "lookbackDays": 30 }
```

## Output

- **Dataset**: one row per opportunity (Act now, Monitor, Product insight), with two table views: *Opportunities* and *Outreach drafts*. Export as JSON, CSV or Excel.
- **Key-value store**:
  - `REPORT.html`: the interactive report (open it in your browser)
  - `COMPETITOR_RADAR`: competitor weaknesses, positioning angles, unmet needs
  - `REVENUE_SCENARIO`: illustrative scenario, only when you give a deal value
  - `STATUS`: run summary

The revenue calculator is an **illustrative scenario based on your assumptions, not a forecast**.

## Use it from an AI agent or your code

**API** (runs take about 2 to 5 minutes, so start a run and fetch the dataset when it finishes):

```bash
curl -X POST "https://api.apify.com/v2/acts/<username>~leadbridge/runs" \
  -H "Authorization: Bearer <APIFY_TOKEN>" -H "Content-Type: application/json" \
  -d '{"productUrl": "https://your-product.com"}'
```

**MCP**: add the Apify MCP server (`https://mcp.apify.com`) to Claude, Cursor or any MCP client, and your agent can run this Actor as a tool.

## Pricing

Pay per event. You only pay for work done:

| Event | When it's charged |
|---|---|
| `product-profile-analyzed` | Once per run, after your product is profiled |
| `complaint-evaluated` | Per post the AI qualifies (after the free filter) |
| `high-intent-lead-drafted` | Per Act-now opportunity delivered with drafts |

AI usage runs through your Apify account via the OpenRouter proxy. Set a maximum cost per run in Apify, and the Actor stops gracefully when it is reached.

## Good to know

- Runs take about 1.5 to 5 minutes, depending on how much it finds.
- Results depend on how much people are complaining publicly in your category. A longer lookback window and adding known competitors find more.
- Built for B2B software. It works best when your website clearly states what you offer.
- Always review drafts before posting, and follow each community's self-promotion rules.
