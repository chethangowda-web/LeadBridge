"""Render the HTML report to a downloadable PDF with headless Chromium (Playwright).

Best effort: if Playwright or Chromium is unavailable, or rendering fails, return None
and the run continues with REPORT.html only.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

PDF_TIMEOUT_SECONDS = 60


async def _render(html: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            # Show every collapsible section and every draft in the PDF.
            await page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
            await page.emulate_media(media="print")
            return await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
            )
        finally:
            await browser.close()


async def html_to_pdf(html: str) -> bytes | None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        logger.info("Playwright not installed; skipping REPORT.pdf")
        return None
    try:
        return await asyncio.wait_for(_render(html), timeout=PDF_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("PDF rendering failed (%s: %s); REPORT.html is still available", type(exc).__name__, exc)
        return None
