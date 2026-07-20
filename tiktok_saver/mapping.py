"""Surface <-> TikTok web-API endpoint map.

This module exists to contain ONE dangerous piece of TikTok trivia in a single
place with a test around it: TikTok's endpoint names are inverted relative to
the UI.

    UI tab            wire endpoint                         internal word
    ----------------  ------------------------------------  -------------
    Favorites (saved) /api/user/collect/item_list/          "collect"
    Likes  (heart)    /api/favorite/item_list               "favorite"  (!)

So `favorite` on the wire means the LIKED tab, and a bookmark/save is called a
`collect`. Getting this backwards silently exports the wrong list. Every
consumer must route through SURFACES here, never hand-write an endpoint.

Endpoint paths verified 2026-07-13 against maintained reverse-engineered
clients (davidteather/TikTok-Api user.py liked(); Johnserf-Seed/f2 USER_COLLECT;
victoralvelais/tiktok-collections; DerTarchin/tiktok-downloader) — see
ARCHITECTURE.md "Verified endpoints".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Surface:
    """One exportable list on a TikTok profile.

    key           our stable identifier (also the manifest ``source_type``)
    ui_name       what TikTok calls the tab in the UI
    list_substr   substring that appears in the enumerating XHR's URL; the
                  response-interception handler matches on this
    item_key      the array key inside the JSON response holding the items
    owner_only    True if the tab is only visible to the account owner (needs
                  the logged-in session and a tab click; no public deep-link)
    tab_name      the profile tab whose click reveals this surface, matched by
                  ``page.get_by_role("tab", name=tab_name)``. Verified live
                  2026-07-19: profile tabs are Videos / Favorites / Liked, and
                  the Favorites tab reveals BOTH the collection folder list and
                  the saved-video ("collect") list. There is no Collections tab.
    """

    key: str
    ui_name: str
    list_substr: str
    item_key: str
    owner_only: bool
    tab_name: str


# The three item surfaces plus the folder-list surface. `item_key` is what the
# response JSON nests the items under.
# The collection folder list. Fires on the Favorites-tab click as
# /api/user/collection_list/ (verified live 2026-07-19).
COLLECTIONS_FOLDERS = Surface(
    key="collection_folders",
    ui_name="Collections (folder list)",
    list_substr="collection_list",
    item_key="collectionList",
    owner_only=True,
    tab_name="Favorites",
)

SURFACES: dict[str, Surface] = {
    "collections": Surface(
        key="collection",
        ui_name="Collections (videos inside a folder)",
        # per-folder items come from a direct deep-link, not a tab click, but the
        # folder LIST is revealed by the Favorites tab (see COLLECTIONS_FOLDERS).
        list_substr="collection/item_list",
        item_key="itemList",
        owner_only=True,
        tab_name="Favorites",
    ),
    # Favorites = the BOOKMARK / saved tab = "collect" on the wire.
    "favorites": Surface(
        key="favorites",
        ui_name="Favorites (saved / bookmark tab)",
        list_substr="user/collect/item_list",
        item_key="itemList",
        owner_only=True,
        tab_name="Favorites",
    ),
    # Likes = the HEART tab = "favorite" on the wire. The inversion lives here.
    "liked": Surface(
        key="liked",
        ui_name="Likes (heart tab)",
        list_substr="favorite/item_list",
        item_key="itemList",
        owner_only=True,
        tab_name="Liked",
    ),
}

# The item surfaces a `--surface all` run walks (collection folders are walked
# implicitly as a prerequisite of `collections`).
ITEM_SURFACE_KEYS = ("collections", "favorites", "liked")


def resolve(surface_arg: "str | list[str]") -> list[Surface]:
    """Map CLI --surface value(s) to the Surface objects to enumerate.

    Accepts a single value or a list, e.g. ``["collections", "favorites"]``.
    ``all`` expands to every item surface. Order preserved, duplicates dropped.
    """
    args = [surface_arg] if isinstance(surface_arg, str) else list(surface_arg)
    if "all" in args:
        return [SURFACES[k] for k in ITEM_SURFACE_KEYS]
    out: list[Surface] = []
    seen: set[str] = set()
    for a in args:
        if a not in SURFACES:
            raise KeyError(
                f"unknown surface {a!r}; valid: "
                f"{', '.join(('all',) + ITEM_SURFACE_KEYS)}"
            )
        if a not in seen:
            seen.add(a)
            out.append(SURFACES[a])
    return out


def keys_for(surface_arg: "str | list[str]") -> list[str] | None:
    """The manifest source_type keys for the selected surfaces, or None for
    'all' (meaning: don't filter downloads by membership)."""
    args = [surface_arg] if isinstance(surface_arg, str) else list(surface_arg)
    if "all" in args:
        return None
    return [s.key for s in resolve(args)]
