"""Optional completeness cross-check against TikTok's official data export.

TikTok's in-app "Download your data" (Settings -> Account -> Download your data
-> JSON) contains your Favorites and Likes but NOT your named Collections
(verified 2026-07-13 against four independent parsers + two real user_data.json
files — see ARCHITECTURE.md). So this is a best-effort *reconciliation* layer,
not the spine: it flags favorites/likes that appear in the official export but
were missed by the live scroll (rare, but the export is authoritative for those
two flat lists).

Schema of the relevant slice (confirmed):
    { "Activity": {
        "Favorite Videos": { "FavoriteVideoList": [ {"Date": "...", "Link": "..."} ] },
        "Like List":       { "ItemFavoriteList": [ {"Date": "...", "Link": "..."} ] } } }
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .manifest import Manifest

_ID_RE = re.compile(r"/(?:video|photo)/(\d+)")


def _extract_links(export: dict, section: str, list_key: str) -> list[str]:
    activity = export.get("Activity") or export.get("activity") or {}
    node = activity.get(section) or {}
    arr = node.get(list_key) or []
    out = []
    for entry in arr:
        link = entry.get("Link") or entry.get("link")
        if link:
            out.append(link)
    return out


def reconcile(manifest: Manifest, export_path: str | Path, log=print) -> dict[str, list[str]]:
    """Compare the official export's Favorites + Likes against the manifest.

    Returns {'favorites_missing': [...], 'liked_missing': [...]} — links present
    in the export but with no matching post id in the manifest.
    """
    export = json.loads(Path(export_path).read_text(encoding="utf-8"))
    have = manifest.all_canonical_urls()
    have_ids = {m.group(1) for u in have if (m := _ID_RE.search(u))}

    result: dict[str, list[str]] = {}
    for label, section, key in (
        ("favorites_missing", "Favorite Videos", "FavoriteVideoList"),
        ("liked_missing", "Like List", "ItemFavoriteList"),
    ):
        links = _extract_links(export, section, key)
        missing = []
        for link in links:
            m = _ID_RE.search(link)
            if not m or m.group(1) not in have_ids:
                missing.append(link)
        result[label] = missing
        log(f"  {section}: {len(links)} in export, {len(missing)} not in manifest")
    return result
