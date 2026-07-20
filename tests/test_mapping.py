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
    # All three are owner-only: the folder list, Favorites and Liked all live
    # behind owner-only profile tabs (verified live 2026-07-19).
    assert mapping.SURFACES["favorites"].owner_only is True
    assert mapping.SURFACES["liked"].owner_only is True
    assert mapping.SURFACES["collections"].owner_only is True


def test_tab_names_match_live_ui():
    # Verified live 2026-07-19: profile tabs are Videos / Favorites / Liked.
    # Collections + Favorites both open from the Favorites tab; Likes from Liked.
    assert mapping.SURFACES["favorites"].tab_name == "Favorites"
    assert mapping.SURFACES["collections"].tab_name == "Favorites"
    assert mapping.COLLECTIONS_FOLDERS.tab_name == "Favorites"
    assert mapping.SURFACES["liked"].tab_name == "Liked"


def test_resolve_all():
    got = [s.key for s in mapping.resolve("all")]
    assert got == ["collection", "favorites", "liked"]


def test_resolve_single():
    assert [s.key for s in mapping.resolve("liked")] == ["liked"]


def test_resolve_multiple_preserves_order_and_dedups():
    got = [s.key for s in mapping.resolve(["collections", "favorites", "collections"])]
    assert got == ["collection", "favorites"]      # deduped, order kept, no liked


def test_resolve_all_in_list_expands():
    assert [s.key for s in mapping.resolve(["collections", "all"])] == \
        ["collection", "favorites", "liked"]


def test_keys_for_all_is_none():
    # 'all' => no membership filter on downloads.
    assert mapping.keys_for(["all"]) is None
    assert mapping.keys_for("all") is None


def test_keys_for_subset():
    assert mapping.keys_for(["collections", "favorites"]) == ["collection", "favorites"]


def test_resolve_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        mapping.resolve("bookmarks")
