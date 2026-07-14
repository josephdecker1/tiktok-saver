"""Capture: response-body absorption, dedup, hasMore, and DOM-independent
completion. No browser involved — we feed the handler raw JSON bodies."""
from tiktok_saver.enumerate import Capture


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
