# Architecture

## The core idea: separate ENUMERATION from DOWNLOAD

Every failed TikTok exporter conflates two different jobs:

1. **Enumeration** — *which* posts are in which list (Collections, Favorites, Likes).
2. **Download** — fetch the actual video/image bytes.

This tool keeps them apart. Enumeration is done **once, in-session, by reading the
logged-in page's own JSON replies** via Playwright response interception. Download is
delegated to the two maintained CLIs (**yt-dlp** for videos, **gallery-dl** for photo
slideshows), fed explicit per-post URLs.

Why the split matters: no single CLI enumerates all three surfaces, and the one that
*claims* to (yt-dlp's `tiktok:collection`) is **currently returning empty** — see below.
So enumeration must be owned by the browser layer, not outsourced.

## Why response interception beats DOM scraping

The old tool (`src/browser.py`, now removed) drove Selenium and scraped rendered DOM
through **build-generated hashed CSS classes** (`.css-1uqux2o-DivItemContainerV2`) and an
**absolute XPath** (`/html/body/div[1]/div[2]/…`) to click the Favorites tab. Those
change on every TikTok deploy — that is exactly why it rotted.

Response interception reads the SPA's own `*_item_list` JSON responses. The logged-in page
signs its own requests (X-Gnarly); we just read the answers. This is:

- **Immune to CSS/class churn** — we never parse rendered HTML.
- **Free metadata** — the JSON carries caption, author (incl. `secUid`), stats, music,
  timestamps, and the `imagePost` slideshow structure.
- **Signing-free** — we never compute X-Gnarly/X-Bogus ourselves.

The only unavoidable DOM interaction is **one click** to open the owner-only
Favorites / Liked tab, done via Playwright's stable `data-e2e` test-id attribute (never
hashed CSS or XPath), with a text-label fallback.

## Verified endpoints (2026-07-13)

Every path below was confirmed by reading the source of maintained reverse-engineered
clients, and adversarially re-verified against the raw files. **Note the inverted names**
— this is the single most dangerous trap, guarded by `mapping.py` + `tests/test_mapping.py`:

| UI surface | Wire endpoint | Item key | Source |
|---|---|---|---|
| Collections — folder list | `/api/user/collection_list/` | `collectionList` | victoralvelais/tiktok-collections, DerTarchin/tiktok-downloader |
| Collections — items in a folder | `/api/collection/item_list/` (`collectionId`, `count≤30`, `cursor`, `sourceType=113`) | `itemList` | josephyooo/tiktok-api-cli, DerTarchin, victoralvelais |
| **Favorites** (saved / bookmark tab) | **`/api/user/collect/item_list/`** — a bookmark is a "collect" | `itemList` | Johnserf-Seed/f2 (`USER_COLLECT`, `用户收藏`), victoralvelais |
| **Likes** (heart tab) | **`/api/favorite/item_list`** — ⚠️ `favorite` = LIKED | `itemList` | davidteather/TikTok-Api `user.py` `liked()` |

## Why NOT the alternatives

| Rejected | Reason |
|---|---|
| **yt-dlp `tiktok:collection` as enumerator** | Live probe returned **0 items** on a known-good 9-item collection; direct curl of `/api/collection/item_list/` gave `statusCode:100001`, no `itemList`. Open issue [#13134](https://github.com/yt-dlp/yt-dlp/issues/13134) ("API returns empty response", updated 2026-07-02). yt-dlp has **no** favorites/liked extractor at all (feature request #1584 open since 2021). Kept as a **download** engine only. |
| **gallery-dl alone** | Covers liked/saved + downloads slideshows, but has **no named-collection extractor**. Collections are the half most wanted. Kept as the **photo** download engine. |
| **Hand-built signed API client** | The live signature is **X-Gnarly** (webmssdk 5.1.3-ZTCA); X-Bogus is its deprecated predecessor. Porting a signer is high-maintenance and likely already stale. Interception sidesteps signing entirely. |
| **Official "Download your data" export** | Contains Favorites + Likes but **NOT Collections** (verified against 4 parsers + 2 real `user_data.json` files). It's IDs-not-videos and lags 1–4 days. Kept as an optional `reconcile` completeness check, not the spine. |
| **Display API / Data Portability API** | No scope returns saved/liked/collected content. Dead end. |

## Storage schema

SQLite, decoupling membership from media so a video in three collections is one `posts`
row + three `memberships` rows (the old flat single-FK schema couldn't represent this):

- `posts` — one row per unique post; **full `raw_json` blob** = link-rot insurance (the
  list keeps its metadata after TikTok delists a video). `author_sec_uid` stored explicitly
  (survives handle changes).
- `memberships` — many-to-many `(video_id, source_type, source_id)`.
- `media_files` — one row per downloaded file (a slideshow ⇒ N image rows).
- `download_status` — per-post state machine (`pending|done|gone|private|regionlocked|error`);
  replaces `failed_downloads.csv`. Terminal states are never retried.

## Idempotent re-run

1. Re-enumerate (cheap, cookied) → UPSERT `last_seen_ts`, add new memberships, transition
   vanished posts to `gone` (never delete).
2. Download only where **no `media_files` row exists AND state ∉ terminal**.
3. Terminal states short-circuit — nothing re-downloads.

## Download details

- **Videos** → yt-dlp default format (`play_addr`, **watermark-free**; never
  `download_addr`), `--write-info-json`, cookies via exported `cookies.txt`.
- **Photos** (`post_type='image'`) → gallery-dl `photos:true`; yt-dlp only grabs a
  slideshow's audio. Routing is by `post_type` from the manifest, **not** a `/photo/` URL
  filter (the old tool's filter silently dropped every slideshow).
- **`curl_cffi` installed** so yt-dlp's `impersonate=True` has a target (lowers block risk).
- **`playAddr` is never persisted** — it's a signed, expiring, cookie+Referer-coupled CDN
  URL. Always re-resolved fresh at download time by the CLIs.

## Empirical unknowns (resolved by the first live run against your account)

These cannot be known a priori and are flagged honestly rather than assumed:

1. **Exact `data-e2e` values for the Favorites/Liked/Collections tab buttons.** The item
   *cards* use `favorites-item` / `user-liked-item` (confirmed in maintained scrapers), but
   the *tab* buttons' ids are unconfirmed. `enumerate.py` tries several ids then falls back
   to a text-label click. **This is the most likely thing to need a one-line tweak** after
   the first run.
2. Whether the collection/favorite endpoints accept **unsigned same-origin fetch** for your
   account (an optional speed upgrade over auto-scroll; interception works regardless).
3. Real rate-limit ceiling for first-party cookied pagination (pacing is a conservative
   0.9 s/scroll by default).
4. Total volume and photo-post prevalence.
5. The irreducible residue (deleted / private-no-access / region-locked) — quantified by
   `download_status` after the first `download` pass.

The cheapest de-risking probe: `tiktok-saver login` then `tiktok-saver enumerate --surface collections`.
