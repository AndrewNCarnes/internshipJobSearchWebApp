"""
test_harness.py -- runs every scraper end-to-end against fake ATS responses.

This sandbox can't reach greenhouse/workday/oracle, so instead of live HTTP
each scraper is fed a realistic payload in the exact shape its ATS returns.
That verifies the parsing, pagination, filtering, URL construction and DB
write path. It does NOT verify live CSS selectors or that the endpoints are
still up -- only a real run does that.

Also records browser lifecycle calls so leaked chromium processes show up.
"""
import sys
import os
import types
import sqlite3
import contextlib

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scrapers"))

LIFECYCLE = []          # ("browser_launched"/"browser_closed"/"context_closed")
HTML_CARDS = []         # cards returned by query_selector_all
HTML_PAGES = []         # successive card sets; each goto() pops one
HTML_BY_SELECTOR = {}   # selector -> cards, for multi-strategy scrapers
INTERCEPT_PAYLOADS = [] # payloads pushed to page.on("response") handlers
JSON_ROUTES = {}        # url-substring -> list of payloads (one per page call)
GOTO_RAISES = False     # simulate the page failing to load
REPLAY_PAYLOAD = None   # post_data seen on intercepted requests
PAGE_TITLE = ''         # for maintenance-page detection
PAGE_BODY = ''


# --------------------------------------------------------------------------
# fake HTTP response
# --------------------------------------------------------------------------
class FakeResp:
    def __init__(self, payload, ok=True, status=200, url=""):
        self._payload = payload
        self.ok = ok
        self.status = status
        self.status_code = status   # requests-style alias
        self.status_text = "OK" if ok else "Forbidden"
        self.url = url
        self.request = types.SimpleNamespace(
            method="POST",
            post_data=REPLAY_PAYLOAD,
            headers={"Content-Type": "application/json"},
        )

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status}")


def _next_payload(url):
    for key, queue in JSON_ROUTES.items():
        if key in url:
            return queue.pop(0) if queue else {}
    return {}


class FailResp(FakeResp):
    """Simulates a 403 from Workday/Cloudflare."""
    def __init__(self, status=403):
        super().__init__({}, ok=False, status=status)


# --------------------------------------------------------------------------
# fake HTML element
# --------------------------------------------------------------------------
class FakeEl:
    def __init__(self, text="", attrs=None, children=None):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or {}

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def query_selector(self, sel):
        return self._children.get(sel)

    def query_selector_all(self, sel):
        c = self._children.get(sel)
        return c if isinstance(c, list) else ([c] if c else [])


# --------------------------------------------------------------------------
# fake playwright
# --------------------------------------------------------------------------
class FakeRequestCtx:
    def _resp(self, url):
        p = _next_payload(url)
        if p == "__403__":
            return FailResp()
        if p == "__boom__":
            raise RuntimeError("connection reset by peer")
        return FakeResp(p, url=url)

    def post(self, url, headers=None, data=None, **kw):
        return self._resp(url)

    def get(self, url, headers=None, **kw):
        return self._resp(url)


class FakePage:
    def __init__(self):
        self._handlers = []

    def on(self, event, handler):
        if event == "response":
            self._handlers.append(handler)

    def goto(self, url, **kw):
        if GOTO_RAISES:
            raise RuntimeError("net::ERR_CONNECTION_RESET")
        if HTML_PAGES:
            HTML_CARDS[:] = HTML_PAGES.pop(0)
        # fire any intercepted-API payloads the scraper is listening for
        for payload in INTERCEPT_PAYLOADS:
            fake = FakeResp(payload, url="https://careers.example.com/widgets/refine")
            for h in self._handlers:
                h(fake)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, sel, **kw):
        if not HTML_CARDS:
            raise RuntimeError("Timeout 10000ms waiting for selector")

    def query_selector_all(self, sel):
        if HTML_BY_SELECTOR:
            return HTML_BY_SELECTOR.get(sel, [])
        return HTML_CARDS

    def query_selector(self, sel):
        if HTML_BY_SELECTOR:
            got = HTML_BY_SELECTOR.get(sel, [])
            return got[0] if got else None
        return None

    def title(self):
        return PAGE_TITLE

    def inner_text(self, sel):
        return PAGE_BODY

    def evaluate(self, js):
        pass


class FakeContext:
    def __init__(self):
        self.request = FakeRequestCtx()

    def new_page(self):
        return FakePage()

    def close(self):
        LIFECYCLE.append("context_closed")


class FakeBrowser:
    def new_context(self, **kw):
        return FakeContext()

    def new_page(self):
        return FakePage()

    def close(self):
        LIFECYCLE.append("browser_closed")


class FakeChromium:
    def launch(self, **kw):
        LIFECYCLE.append(("browser_launched", kw.get("headless", True)))
        return FakeBrowser()


class FakeP:
    chromium = FakeChromium()


@contextlib.contextmanager
def fake_sync_playwright():
    yield FakeP()


def install_mocks():
    pw = types.ModuleType("playwright")
    pw_sync = types.ModuleType("playwright.sync_api")
    pw_sync.sync_playwright = fake_sync_playwright
    pw.sync_api = pw_sync
    sys.modules["playwright"] = pw
    sys.modules["playwright.sync_api"] = pw_sync

    req = types.ModuleType("requests")

    def fake_get(url, headers=None, **kw):
        p = _next_payload(url)
        if p == "__403__":
            return FakeResp({}, ok=False, status=403, url=url)
        if p == "__boom__":
            raise RuntimeError("connection reset by peer")
        return FakeResp(p, url=url)

    req.get = fake_get
    sys.modules["requests"] = req


def reset(json_routes=None, html_cards=None, intercept=None, goto_raises=False,
          replay_payload=None, html_pages=None, page_title="", page_body="",
          html_by_selector=None):
    global GOTO_RAISES, REPLAY_PAYLOAD
    GOTO_RAISES = goto_raises
    REPLAY_PAYLOAD = replay_payload
    global PAGE_TITLE, PAGE_BODY
    PAGE_TITLE = page_title
    PAGE_BODY = page_body
    JSON_ROUTES.clear()
    JSON_ROUTES.update(json_routes or {})
    HTML_CARDS[:] = html_cards or []
    HTML_PAGES[:] = html_pages or []
    HTML_BY_SELECTOR.clear()
    HTML_BY_SELECTOR.update(html_by_selector or {})
    INTERCEPT_PAYLOADS[:] = intercept or []
    LIFECYCLE[:] = []