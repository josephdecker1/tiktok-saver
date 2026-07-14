"""Swap-guard for the endpoint-name inversion.

These are change-detectors, not live-correctness proofs: they lock in the
favorites->collect / liked->favorite pairing so an unrelated edit can't quietly
swap the two strings and make the tool export the wrong list. Whether these
endpoints are still what live TikTok serves is a disclosed empirical unknown
(ARCHITECTURE.md), confirmed only by the first live run.
"""
from tiktok_saver import mapping


def test_favorites_tab_maps_to_collect_endpoint():
    # The Favorites (saved/bookmark) UI tab is "collect" on the wire.
    assert mapping.SURFACES["favorites"].list_substr == "user/collect/item_list"


def test_liked_tab_maps_to_favorite_endpoint():
    # The Likes (heart) UI tab is the INVERTED "favorite" endpoint.
    assert mapping.SURFACES["liked"].list_substr == "favorite/item_list"


def test_inversion_is_not_accidentally_swapped():
    # Explicitly assert the two are not the same and not swapped.
    fav = mapping.SURFACES["favorites"].list_substr
    liked = mapping.SURFACES["liked"].list_substr
    assert "collect" in fav and "collect" not in liked
    assert liked == "favorite/item_list"


def test_owner_only_flags():
    assert mapping.SURFACES["favorites"].owner_only is True
    assert mapping.SURFACES["liked"].owner_only is True
    assert mapping.SURFACES["collections"].owner_only is False


def test_resolve_all():
    got = [s.key for s in mapping.resolve("all")]
    assert got == ["collection", "favorites", "liked"]


def test_resolve_single():
    assert [s.key for s in mapping.resolve("liked")] == ["liked"]


def test_resolve_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        mapping.resolve("bookmarks")
