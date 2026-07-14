"""Enumeration — read the page's OWN JSON replies to learn what's in each list.

This is the durable core. Instead of scraping rendered DOM through hashed CSS
classes (what broke the old tool on every TikTok deploy), we register a
``page.on("response")`` handler BEFORE navigating, then scroll each surface and
capture the ``*_item_list`` / ``collection_list`` JSON the logged-in SPA fetches
for itself. The page signs its own requests (X-Gnarly); we just read the
answers. Immune to CSS churn, and we get full metadata for free.

Completion is keyed off the JSON (``hasMore == false``) plus a plateau counter
(N scrolls with zero new ids) — never off DOM card counts, because virtualized
lists recycle nodes and lie.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from . import mapping
from .manifest import Manifest

SCROLL_PAUSE_S = 0.9          # pace between scrolls; also the anti-bot throttle
PLATEAU_LIMIT = 4             # consecutive zero-new-id scrolls => that surface is done
MAX_SCROLLS = 400            # hard stop so a broken hasMore can't loop forever


@dataclass
class Capture:
    """Accumulates items seen for one surface across all intercepted responses."""

    item_key: str
    items: dict[str, dict] = field(default_factory=dict)   # id -> raw item
    order: list[str] = field(default_factory=list)
    has_more: bool = True

    def absorb(self, payload: dict) -> int:
        """Merge one response body; return count of NEW ids."""
        arr = payload.get(self.item_key) or []
        new = 0
        for it in arr:
            vid = str(it.get("id") or it.get("collectionId") or "")
            if not vid or vid in self.items:
                continue
            self.items[vid] = it
            self.order.append(vid)
            new += 1
        if "hasMore" in payload:
            self.has_more = bool(payload.get("hasMore"))
        return new


def _make_handler(captures: list[tuple[str, Capture]], log: Callable[[str], None]):
    """Return a page.on('response') handler that routes each response to the
    capture whose surface substring matches its URL."""

    def handler(response) -> None:
        url = response.url
        for substr, cap in captures:
            if substr in url:
                try:
                    body = response.json()
                except Exception:
                    return  # redirect/cached/streamed body — nothing to read
                if isinstance(body, dict):
                    n = cap.absorb(body)
                    if n:
                        log(f"    +{n} from {substr} (total {len(cap.items)})")
                return

    return handler


def _autoscroll_until_done(page, cap: Capture, log: Callable[[str], None]) -> None:
    """Scroll to the bottom repeatedly until the API says hasMore=false or the
    id count plateaus."""
    plateau = 0
    for i in range(MAX_SCROLLS):
        before = len(cap.items)
        page.mouse.wheel(0, 20000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(SCROLL_PAUSE_S)
        gained = len(cap.items) - before
        if gained == 0:
            plateau += 1
        else:
            plateau = 0
        if not cap.has_more and plateau >= 2:
            log(f"    hasMore=false and settled after {i + 1} scrolls")
            return
        if plateau >= PLATEAU_LIMIT:
            log(f"    plateaued ({plateau} empty scrolls) after {i + 1} scrolls")
            return
    log(f"    hit MAX_SCROLLS={MAX_SCROLLS} — surface may be incomplete")


def _open_owner_tab(page, surface: mapping.Surface, log: Callable[[str], None]) -> bool:
    """Click the owner-only Favorites/Liked tab via the STABLE data-e2e test id
    (never hashed CSS or XPath). Returns True if the tab opened."""
    # data-e2e values confirmed live in maintained scrapers (stepney141/favs,
    # LikeVault): favorites tab -> favorites-item cards; liked -> user-liked-item.
    tab_testids = {
        "favorites": ["favorites-tab", "favorites"],
        "liked": ["liked-tab", "like-tab", "user-liked"],
    }
    page.set_test_id_attribute("data-e2e")
    for tid in tab_testids.get(surface.key, []):
        try:
            el = page.get_by_test_id(tid)
            if el.count() > 0:
                el.first.click(timeout=4000)
                log(f"    opened tab via data-e2e={tid}")
                time.sleep(SCROLL_PAUSE_S)
                return True
        except Exception:
            continue
    # Fallback: role-based text match on the tab label.
    for label in ("Favorites", "Liked", "Likes"):
        try:
            page.get_by_text(label, exact=True).first.click(timeout=3000)
            log(f"    opened tab via text={label}")
            time.sleep(SCROLL_PAUSE_S)
            return True
        except Exception:
            continue
    return False


def enumerate_collections(
    context,
    username: str,
    manifest: Manifest,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Walk the collection FOLDER list, then each folder's videos.

    Returns {collection_name: item_count}. Folders are public (secUid-addressed)
    so no owner tab click is needed for the folder list itself.
    """
    page = context.new_page()
    folder_cap = Capture(item_key=mapping.COLLECTIONS_FOLDERS.item_key)
    caps = [(mapping.COLLECTIONS_FOLDERS.list_substr, folder_cap)]
    page.on("response", _make_handler(caps, log))

    log(f"  collections: loading @{username} profile")
    page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
    time.sleep(2)
    # The collections tab is public; open it and let the folder-list XHR fire.
    _open_owner_tab_generic(page, ["Collections", "collections-tab"], log)
    _autoscroll_until_done(page, folder_cap, log)

    result: dict[str, int] = {}
    folders = [folder_cap.items[i] for i in folder_cap.order]
    log(f"  found {len(folders)} collection folder(s)")

    for folder in folders:
        cid = str(folder.get("collectionId") or folder.get("id") or "")
        name = folder.get("name") or cid
        if not cid:
            continue
        item_cap = Capture(item_key=mapping.SURFACES["collections"].item_key)
        # A fresh page per folder keeps the interception clean and the deep-link
        # avoids any fragile tab-click inside a folder.
        fpage = context.new_page()
        fpage.on(
            "response",
            _make_handler([(mapping.SURFACES["collections"].list_substr, item_cap)], log),
        )
        slug = _slugify(name)
        log(f"  collection '{name}' ({cid}): loading")
        fpage.goto(
            f"https://www.tiktok.com/@{username}/collection/{slug}-{cid}",
            wait_until="domcontentloaded",
        )
        time.sleep(2)
        _autoscroll_until_done(fpage, item_cap, log)
        _persist(manifest, item_cap, source_type="collection", source_id=cid, source_name=name)
        result[name] = len(item_cap.items)
        fpage.close()

    manifest.commit()
    return result


def enumerate_item_surface(
    context,
    username: str,
    surface: mapping.Surface,
    manifest: Manifest,
    log: Callable[[str], None] = print,
) -> int:
    """Walk the Favorites (saved) or Likes surface. Both are owner-only tabs."""
    page = context.new_page()
    cap = Capture(item_key=surface.item_key)
    page.on("response", _make_handler([(surface.list_substr, cap)], log))

    log(f"  {surface.ui_name}: loading @{username} profile")
    page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
    time.sleep(2)
    if surface.owner_only and not _open_owner_tab(page, surface, log):
        log(f"  WARNING: could not open the {surface.ui_name} tab — is this YOUR "
            f"account and are you logged in? (owner-only tab)")
    _autoscroll_until_done(page, cap, log)
    _persist(manifest, cap, source_type=surface.key, source_id="_self",
             source_name=surface.key)
    manifest.commit()
    page.close()
    return len(cap.items)


def _persist(
    manifest: Manifest,
    cap: Capture,
    source_type: str,
    source_id: str,
    source_name: str,
) -> None:
    for pos, vid in enumerate(cap.order):
        item = cap.items[vid]
        try:
            real_id = manifest.upsert_post(item)
        except ValueError:
            continue
        manifest.add_membership(real_id, source_type, source_id, source_name, pos)
        manifest.ensure_status(real_id, source_type)


def _open_owner_tab_generic(page, candidates, log) -> bool:
    page.set_test_id_attribute("data-e2e")
    for c in candidates:
        try:
            el = page.get_by_test_id(c)
            if el.count() > 0:
                el.first.click(timeout=4000)
                time.sleep(SCROLL_PAUSE_S)
                return True
        except Exception:
            pass
        try:
            page.get_by_text(c, exact=True).first.click(timeout=3000)
            time.sleep(SCROLL_PAUSE_S)
            return True
        except Exception:
            pass
    return False


def _slugify(name: str) -> str:
    """TikTok collection URLs are ``<slug>-<id>``; the slug is cosmetic (the id
    is what resolves) but we mimic the real format for cleanliness."""
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "collection"
