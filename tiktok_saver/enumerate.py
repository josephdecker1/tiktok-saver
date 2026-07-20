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


def _hits_known_watermark(order: list[str], known_ids: set[str], k: int) -> bool:
    """True if scanning ``order`` (newest-first capture order) ever reaches ``k``
    consecutive already-known ids. Pure + unit-tested: this is the incremental
    stop rule. New saves sit at the top, so once we cross the newest-known
    boundary everything below is old — k consecutive knowns means we're past it.
    The k-run (not a single hit) tolerates a pinned/promoted item near the top."""
    if k <= 0:
        return False                 # no watermark => never early-stop
    consec = 0
    for vid in order:
        consec = consec + 1 if vid in known_ids else 0
        if consec >= k:
            return True
    return False


def _autoscroll_until_done(
    page,
    cap: Capture,
    log: Callable[[str], None],
    known_ids: "set[str] | None" = None,
    stop_after_known: int = 0,
) -> None:
    """Scroll to the bottom repeatedly until the API says hasMore=false or the
    id count plateaus.

    When ``known_ids`` and ``stop_after_known`` are given (incremental sync),
    also stop once ``stop_after_known`` consecutive already-known ids appear in
    capture order — the early-stop that makes a re-sync cheap."""

    def _early_stop() -> bool:
        return bool(known_ids) and stop_after_known > 0 and _hits_known_watermark(
            cap.order, known_ids, stop_after_known)

    if _early_stop():
        # New saves above the known run (if any) are already in cap.order and get
        # persisted by the caller — so don't claim "nothing new"; just note we hit
        # the watermark on the first page.
        log(f"    incremental: reached known watermark on the first page — "
            f"no further scrolling")
        return

    plateau = 0
    for i in range(MAX_SCROLLS):
        before = len(cap.items)
        page.mouse.wheel(0, 20000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(SCROLL_PAUSE_S)
        if _early_stop():
            log(f"    incremental: hit known watermark after {i + 1} scrolls — stopping")
            return
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


def _click_tab(page, tab_name: str, log: Callable[[str], None]) -> bool:
    """Open a profile tab (Favorites / Liked) by its ACCESSIBLE ROLE.

    Verified live 2026-07-19: ``get_by_role("tab", name="Favorites"|"Liked")``
    clicks the right tab. This deliberately avoids ``get_by_test_id`` — Playwright's
    test-id defaults to ``data-testid`` (TikTok uses ``data-e2e``), which is what
    made the earlier build crash — and avoids hashed CSS/XPath entirely. Falls
    back to a visible-text click.
    """
    try:
        page.get_by_role("tab", name=tab_name).first.click(timeout=6000)
        log(f"    opened '{tab_name}' tab")
        time.sleep(SCROLL_PAUSE_S)
        return True
    except Exception:
        pass
    try:
        page.get_by_text(tab_name, exact=True).first.click(timeout=4000)
        log(f"    opened '{tab_name}' tab (text fallback)")
        time.sleep(SCROLL_PAUSE_S)
        return True
    except Exception as e:
        log(f"    could not open '{tab_name}' tab: {e}")
        return False


def enumerate_collections(
    context,
    username: str,
    manifest: Manifest,
    log: Callable[[str], None] = print,
    incremental: bool = False,
    stop_after_known: int = 3,
) -> dict[str, int]:
    """Walk the collection FOLDER list, then each folder's videos.

    Returns {collection_name: item_count}. The folder LIST is always fetched in
    full (it's small, and new folders must be detected). When ``incremental``,
    each folder's items stop early once ``stop_after_known`` consecutive
    already-known ids appear — a brand-new folder has no known ids, so it fetches
    in full.
    """
    page = context.new_page()
    folder_cap = Capture(item_key=mapping.COLLECTIONS_FOLDERS.item_key)
    caps = [(mapping.COLLECTIONS_FOLDERS.list_substr, folder_cap)]
    page.on("response", _make_handler(caps, log))

    log(f"  collections: loading @{username} profile")
    page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
    time.sleep(2)
    # The folder list (/api/user/collection_list/) fires when the FAVORITES tab
    # opens — there is no separate Collections tab (verified live 2026-07-19).
    if not _click_tab(page, mapping.COLLECTIONS_FOLDERS.tab_name, log):
        log("  WARNING: could not open Favorites tab — is this YOUR account, "
            "logged in? (collections live under Favorites)")
    _autoscroll_until_done(page, folder_cap, log)   # folder list: always full

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
        known = manifest.known_video_ids("collection", cid) if incremental else None
        _autoscroll_until_done(fpage, item_cap, log,
                               known_ids=known, stop_after_known=stop_after_known)
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
    incremental: bool = False,
    stop_after_known: int = 3,
) -> int:
    """Walk the Favorites (saved) or Likes surface. Both are owner-only tabs.

    When ``incremental``, stop scrolling once ``stop_after_known`` consecutive
    already-known ids appear (the newest saves sit at the top)."""
    page = context.new_page()
    cap = Capture(item_key=surface.item_key)
    page.on("response", _make_handler([(surface.list_substr, cap)], log))

    log(f"  {surface.ui_name}: loading @{username} profile")
    page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
    time.sleep(2)
    if surface.owner_only and not _click_tab(page, surface.tab_name, log):
        log(f"  WARNING: could not open the '{surface.tab_name}' tab — is this YOUR "
            f"account and are you logged in? (owner-only tab)")
    known = manifest.known_video_ids(surface.key) if incremental else None
    _autoscroll_until_done(page, cap, log,
                           known_ids=known, stop_after_known=stop_after_known)
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
        manifest.ensure_status(real_id)


def _slugify(name: str) -> str:
    """TikTok collection URLs are ``<slug>-<id>``; the slug is cosmetic (the id
    is what resolves) but we mimic the real format for cleanliness."""
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "collection"
