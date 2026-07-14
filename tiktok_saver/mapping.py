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
    """

    key: str
    ui_name: str
    list_substr: str
    item_key: str
    owner_only: bool


# The three item surfaces plus the folder-list surface. `item_key` is what the
# response JSON nests the items under.
COLLECTIONS_FOLDERS = Surface(
    key="collection_folders",
    ui_name="Collections (folder list)",
    list_substr="collection_list",
    item_key="collectionList",
    owner_only=False,
)

SURFACES: dict[str, Surface] = {
    "collections": Surface(
        key="collection",
        ui_name="Collections (videos inside a folder)",
        list_substr="collection/item_list",
        item_key="itemList",
        owner_only=False,
    ),
    # Favorites = the BOOKMARK / saved tab = "collect" on the wire.
    "favorites": Surface(
        key="favorites",
        ui_name="Favorites (saved / bookmark tab)",
        list_substr="user/collect/item_list",
        item_key="itemList",
        owner_only=True,
    ),
    # Likes = the HEART tab = "favorite" on the wire. The inversion lives here.
    "liked": Surface(
        key="liked",
        ui_name="Likes (heart tab)",
        list_substr="favorite/item_list",
        item_key="itemList",
        owner_only=True,
    ),
}

# The item surfaces a `--surface all` run walks (collection folders are walked
# implicitly as a prerequisite of `collections`).
ITEM_SURFACE_KEYS = ("collections", "favorites", "liked")


def resolve(surface_arg: str) -> list[Surface]:
    """Map a CLI --surface value to the Surface objects to enumerate."""
    if surface_arg == "all":
        return [SURFACES[k] for k in ITEM_SURFACE_KEYS]
    if surface_arg not in SURFACES:
        raise KeyError(
            f"unknown surface {surface_arg!r}; valid: "
            f"{', '.join(('all',) + ITEM_SURFACE_KEYS)}"
        )
    return [SURFACES[surface_arg]]
