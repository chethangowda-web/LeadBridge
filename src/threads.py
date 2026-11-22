"""Thread context: read the whole Reddit thread, not just a Google snippet.

Between the free filter (Stage 3) and scoring (Stage 4), one batched run of the Reddit scraper
fetches the full text of up to MAX_THREADS candidate threads plus their top replies. Stage 4 then
sees whether the author already solved the problem, and Stage 5 can reply to the real conversation
instead of repeating advice others already gave.

Best effort by design: with no token, no candidates, or any failure, complaints are returned
unchanged and the run continues exactly as before.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.collectors import _run_apify_actor_and_fetch_items
from src.schemas import RawComplaint

logger = logging.getLogger(__name__)

THREAD_URL_RE = re.compile(r"reddit\.com/r/[^/\s]+/comments/([a-z0-9]+)", re.IGNORECASE)
MAX_THREADS = 30
MAX_REPLIES_KEPT = 4
REPLY_CHARS = 240
BODY_CHARS = 1100
MIN_BODY_CHARS = 40
SKIP_AUTHORS = {"automoderator", "[deleted]", "deleted"}
SKIP_BODIES = {"[deleted]", "[removed]"}


@dataclass
class ThreadStats:
    candidates: int = 0
    enriched: int = 0
    replies: int = 0
    note: str = ""


def thread_id(url: str) -> str | None:
    match = THREAD_URL_RE.search(url or "")
    return match.group(1).lower() if match else None


def _squash(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _looks_truncated(text: str) -> bool:
    low = (text or "").lower()
    return "read more" in low or "..." in low[-40:] or "…" in low[-40:]


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _pick_replies(comments: list[dict[str, Any]]) -> list[str]:
    """Top replies for one thread: the original poster's own replies first, then by score."""
    usable = []
    for c in comments:
        body = _squash(str(c.get("body") or ""), 2000)
        author = str(c.get("authorName") or "").lower()
        if len(body) < 15 or body.lower() in SKIP_BODIES or author in SKIP_AUTHORS or c.get("stickied"):
            continue
        usable.append((bool(c.get("isSubmitter")), _int(c.get("score")), body))
    usable.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = []
    for is_op, _score, body in usable[:MAX_REPLIES_KEPT]:
        text = body[:REPLY_CHARS] + ("..." if len(body) > REPLY_CHARS else "")
        out.append(f"[OP] {text}" if is_op else text)
    return out


async def enrich_threads(
    complaints: list[RawComplaint],
    apify_token: str | None,
    max_threads: int = MAX_THREADS,
) -> tuple[list[RawComplaint], ThreadStats]:
    stats = ThreadStats()
    if not apify_token:
        stats.note = "No Apify token; thread context skipped."
        return complaints, stats

    # Search snippets first (they need the full text most), then Reddit-sourced posts (replies only).
    ordered = sorted(complaints, key=lambda c: 0 if c.source == "google_serp" else 1)
    chosen: dict[str, RawComplaint] = {}
    for item in ordered:
        tid = thread_id(item.post_url)
        if tid and tid not in chosen:
            chosen[tid] = item
        if len(chosen) >= max_threads:
            break
    stats.candidates = len(chosen)
    if not chosen:
        stats.note = "No Reddit threads to read."
        return complaints, stats

    try:
        items = await _run_apify_actor_and_fetch_items(
            actor_id="harshmaur/reddit-scraper-pro",
            run_input={
                "startUrls": [{"url": c.post_url} for c in chosen.values()],
                "maxPostsCount": 1,
                "searchPosts": True,
                "searchComments": False,
                "crawlCommentsPerPost": True,
                "maxCommentsPerPost": 8,
                "maxCommentsCount": 0,
                "aiAnalysis": False,
            },
            apify_token=apify_token,
            timeout_secs=90,
            max_items=max_threads * 12,
        )
    except Exception as exc:
        stats.note = f"Thread read failed ({type(exc).__name__}); continuing with snippets."
        logger.warning("Thread enrichment failed: %s", exc)
        return complaints, stats

    posts: dict[str, dict[str, Any]] = {}
    comments: dict[str, list[dict[str, Any]]] = {}
    for rec in items:
        kind = str(rec.get("dataType") or "").lower()
        if kind == "post":
            pid = str(rec.get("id") or "").removeprefix("t3_").lower()
            if pid:
                posts[pid] = rec
        elif kind == "comment":
            pid = str(rec.get("postId") or "").removeprefix("t3_").lower()
            if pid:
                comments.setdefault(pid, []).append(rec)

    for item in complaints:
        tid = thread_id(item.post_url)
        if not tid or tid not in chosen:
            continue
        post = posts.get(tid)
        replies = _pick_replies(comments.get(tid, []))
        changed = False
        if post:
            body = _squash(str(post.get("body") or post.get("selftext") or ""), BODY_CHARS)
            if len(body) >= MIN_BODY_CHARS and (len(body) > len(item.text) or _looks_truncated(item.text)):
                item.text = f"{item.title}\n{body}".strip() if item.title else body
                changed = True
        if replies:
            item.thread_replies = replies
            stats.replies += len(replies)
            changed = True
        if changed:
            item.thread_enriched = True
            stats.enriched += 1

    logger.info(
        "Thread context: %d/%d threads read, %d replies attached", stats.enriched, stats.candidates, stats.replies
    )
    return complaints, stats
