"""Optional Playwright browser automation for the Elian agent."""
from __future__ import annotations
from typing import Any

_local = __import__("threading").local()

def _ensure():
    if getattr(_local, "page", None) is not None:\n        return _local.page\n    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright غير مثبت. شغل: pip install playwright && playwright install chromium") from exc
    if not hasattr(_ensure, "_pw"):
        _ensure._pw = sync_playwright().start()
    _local.browser = _ensure._pw.chromium.launch(headless=True)\n    _local.page = _local.browser.new_page(viewport={"width": 1440, "height": 1000})\n    _local.page.set_default_timeout(30000)\n    return _local.page\n
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
    b=getattr(_local, "browser", None)\n    if b is not None: b.close()\n    _local.browser=None; _local.page=None\n    return {"closed":True}
