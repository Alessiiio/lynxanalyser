"""Headless browser rendering for checks that need JavaScript-executed content."""

from __future__ import annotations

from app.checks.utils import USER_AGENT

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    PlaywrightTimeoutError = Exception  # type: ignore[misc, assignment]
    async_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_IMPORT_ERROR = (
        "playwright package not installed — run: pip install playwright && playwright install chromium"
    )
else:
    _PLAYWRIGHT_IMPORT_ERROR = None

_NETWORK_IDLE_WAIT_MS = 5000


def _error_result(url: str, error: str, html: str = "", final_url: str | None = None, status_code: int | None = None) -> dict:
    return {
        "html": html,
        "final_url": final_url or url,
        "status_code": status_code,
        "success": False,
        "error": error,
    }


def _success_result(html: str, final_url: str, status_code: int | None) -> dict:
    return {
        "html": html,
        "final_url": final_url,
        "status_code": status_code,
        "success": True,
        "error": None,
    }


def _chromium_missing_message(exc: Exception) -> str | None:
    msg = str(exc)
    if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
        return "Chromium browser not installed for Playwright — run: playwright install chromium"
    return None


async def fetch_rendered_html(url: str, timeout_ms: int = 15000) -> dict:
    """
    Loads a URL with a real headless browser (Playwright/Chromium) so that
    JavaScript-rendered content is included. Returns the fully rendered HTML.
    Designed to be reusable by other checks later (e.g. contact_check.py).
    """
    if async_playwright is None:
        return _error_result(url, _PLAYWRIGHT_IMPORT_ERROR or "playwright not available")

    browser = None
    context = None

    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                hint = _chromium_missing_message(exc)
                return _error_result(url, hint or f"Failed to launch browser: {str(exc)[:160]}")

            try:
                context = await browser.new_context(user_agent=USER_AGENT)
                page = await context.new_page()
                status_code: int | None = None
                final_url = url

                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if response is not None:
                        status_code = response.status
                    final_url = page.url

                    # networkidle catches late AJAX renders but can hang on analytics/chat widgets.
                    try:
                        await page.wait_for_load_state("networkidle", timeout=_NETWORK_IDLE_WAIT_MS)
                    except PlaywrightTimeoutError:
                        pass

                    html = await page.content()
                    return _success_result(html, final_url, status_code)

                except PlaywrightTimeoutError:
                    # Prefer partial HTML over a hard failure — the LLM check can still
                    # analyze whatever rendered before the deadline.
                    try:
                        html = await page.content()
                        final_url = page.url
                    except Exception:
                        html = ""

                    if html and len(html) > 100:
                        return _success_result(html, final_url, status_code)

                    return _error_result(
                        url,
                        f"Page load timed out after {timeout_ms}ms",
                        html=html,
                        final_url=final_url,
                        status_code=status_code,
                    )

                except Exception as exc:
                    return _error_result(url, f"Navigation failed: {str(exc)[:160]}")

            finally:
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()

    except Exception as exc:
        hint = _chromium_missing_message(exc)
        return _error_result(url, hint or f"Browser rendering failed: {str(exc)[:160]}")
