"""Optional Playwright browser automation for the Elian agent."""
from __future__ import annotations
from typing import Any

_browser = None
_page = None

def _ensure():
    global _browser, _page
    if _page is not None:
        return _page
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright غير مثبت. شغل: pip install playwright && playwright install chromium") from exc
    if not hasattr(_ensure, "_pw"):
        _ensure._pw = sync_playwright().start()
    _browser = _ensure._pw.chromium.launch(headless=True)
    _page = _browser.new_page(viewport={"width": 1440, "height": 1000})
    _page.set_default_timeout(30000)
    return _page

def browser_open(url: str) -> dict[str, Any]:
    page=_ensure()
    page.goto(str(url), wait_until="domcontentloaded")
    return {"url": page.url, "title": page.title(), "text": page.locator("body").inner_text()[:30000]}

def browser_snapshot() -> dict[str, Any]:
    page=_ensure()
    return {"url":page.url,"title":page.title(),"text":page.locator("body").inner_text()[:30000]}

def browser_click(selector: str) -> dict[str, Any]:
    page=_ensure()
    page.locator(selector).first.click()
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    return browser_snapshot()

def browser_fill(selector: str, value: str) -> dict[str, Any]:
    page=_ensure()
    page.locator(selector).first.fill(str(value))
    return {"url":page.url,"selector":selector,"filled":True}

def browser_press(selector: str, key: str) -> dict[str, Any]:
    page=_ensure()
    page.locator(selector).first.press(str(key))
    return browser_snapshot()

def browser_screenshot(path: str="browser.png") -> dict[str, Any]:
    page=_ensure()
    page.screenshot(path=path, full_page=True)
    return {"path":path}

def browser_close() -> dict[str, Any]:
    global _browser, _page
    if _browser is not None:
        _browser.close()
    _browser=None; _page=None
    return {"closed":True}
