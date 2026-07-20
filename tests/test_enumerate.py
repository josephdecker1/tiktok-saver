"""Capture: response-body absorption, dedup, hasMore, and DOM-independent
completion. No browser involved — we feed the handler raw JSON bodies."""
from tiktok_saver.enumerate import Capture, _hits_known_watermark


def test_absorb_dedups_by_id():
    cap = Capture(item_key="itemList")
    n1 = cap.absorb({"itemList": [{"id": "1"}, {"id": "2"}], "hasMore": True})
    assert n1 == 2
    # Second page repeats id 2 (cached replay) + adds 3.
    n2 = cap.absorb({"itemList": [{"id": "2"}, {"id": "3"}], "hasMore": False})
    assert n2 == 1
    assert set(cap.items) == {"1", "2", "3"}
    assert cap.order == ["1", "2", "3"]      # first-seen order preserved
    assert cap.has_more is False


def test_absorb_ignores_other_keys():
    cap = Capture(item_key="collectionList")
    n = cap.absorb({"collectionList": [{"collectionId": "c1", "name": "X"}], "hasMore": False})
    assert n == 1
    assert cap.items["c1"]["name"] == "X"


def test_absorb_empty_body_is_zero():
    cap = Capture(item_key="itemList")
    assert cap.absorb({}) == 0
    assert cap.absorb({"itemList": []}) == 0


def test_hasmore_absent_leaves_flag_untouched():
    cap = Capture(item_key="itemList")
    cap.absorb({"itemList": [{"id": "1"}], "hasMore": True})
    # A response without hasMore (e.g. a partial) must not flip it to false.
    cap.absorb({"itemList": [{"id": "2"}]})
    assert cap.has_more is True


# ---- incremental early-stop watermark (pure logic) ----

KNOWN = {"k1", "k2", "k3", "k4"}


def test_watermark_hits_after_k_consecutive_known():
    # newest-first: 2 new saves at top, then a run of knowns.
    order = ["new1", "new2", "k1", "k2", "k3"]
    assert _hits_known_watermark(order, KNOWN, 3) is True


def test_watermark_not_hit_when_knowns_interrupted():
    # a promoted/pinned new item breaks the run — never 3 in a row.
    order = ["k1", "k2", "new1", "k3", "k4"]
    assert _hits_known_watermark(order, KNOWN, 3) is False


def test_watermark_all_known_hits():
    assert _hits_known_watermark(["k1", "k2", "k3"], KNOWN, 3) is True


def test_watermark_all_new_never_hits():
    assert _hits_known_watermark(["a", "b", "c", "d"], KNOWN, 3) is False


def test_watermark_empty_order():
    assert _hits_known_watermark([], KNOWN, 3) is False


def test_watermark_k1_stops_on_first_known():
    assert _hits_known_watermark(["new1", "k1"], KNOWN, 1) is True


def test_watermark_k_zero_or_negative_never_hits():
    # A disabled watermark must never claim a hit, even on all-known input.
    assert _hits_known_watermark(["k1", "k2", "k3"], KNOWN, 0) is False
    assert _hits_known_watermark(["k1"], KNOWN, -1) is False


# ---- _autoscroll_until_done early-stop (the incremental branch) ----

class _FakeMouse:
    def wheel(self, *a):
        pass


class _FakePage:
    """Minimal Playwright-Page stand-in: each scroll (evaluate call) delivers the
    next scripted page into the shared Capture, like the SPA fetching more."""
    def __init__(self, cap, scroll_pages):
        self.mouse = _FakeMouse()
        self._cap = cap
        self._pages = list(scroll_pages)
        self.scrolls = 0

    def evaluate(self, *a, **k):
        if self._pages:
            self._cap.absorb({"itemList": self._pages.pop(0), "hasMore": bool(self._pages)})
        self.scrolls += 1


def _no_sleep(monkeypatch):
    import tiktok_saver.enumerate as en
    monkeypatch.setattr(en.time, "sleep", lambda *a, **k: None)
    return en


def test_autoscroll_stops_in_loop_at_watermark(monkeypatch):
    en = _no_sleep(monkeypatch)
    cap = Capture(item_key="itemList")
    cap.absorb({"itemList": [{"id": "n1"}, {"id": "n2"}, {"id": "k1"}], "hasMore": True})
    # scroll 1 delivers k2,k3 → 3 consecutive knowns → stop; later pages must go untouched.
    page = _FakePage(cap, [[{"id": "k2"}, {"id": "k3"}], [{"id": "k4"}], [{"id": "k5"}]])
    en._autoscroll_until_done(page, cap, lambda *_: None,
                              known_ids={"k1", "k2", "k3", "k4", "k5"}, stop_after_known=3)
    assert page.scrolls == 1                 # stopped right after the watermark
    assert "k4" not in cap.items             # never scrolled far enough to fetch k4/k5


def test_autoscroll_stops_before_scrolling_when_first_page_is_known(monkeypatch):
    en = _no_sleep(monkeypatch)
    cap = Capture(item_key="itemList")
    cap.absorb({"itemList": [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}], "hasMore": True})
    page = _FakePage(cap, [[{"id": "k4"}]])
    en._autoscroll_until_done(page, cap, lambda *_: None,
                              known_ids={"k1", "k2", "k3"}, stop_after_known=3)
    assert page.scrolls == 0                 # returned before any scroll


def test_autoscroll_does_not_early_stop_when_disabled(monkeypatch):
    en = _no_sleep(monkeypatch)
    cap = Capture(item_key="itemList")
    # all ids known, but stop_after_known=0 disables the watermark → must run to
    # the normal plateau/hasMore termination, not stop on the known content.
    cap.absorb({"itemList": [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}], "hasMore": False})
    page = _FakePage(cap, [])
    en._autoscroll_until_done(page, cap, lambda *_: None,
                              known_ids={"k1", "k2", "k3"}, stop_after_known=0)
    assert page.scrolls >= 2                  # did NOT early-stop; reached plateau exit
