# TikTok Saver

Durable, first-party export of **your own** TikTok **Collections** (named folders),
**Favorites** (saved/bookmarks) and **Likes** — the list *and* the videos.

Runs locally against your own logged-in browser session. It reads the TikTok web app's
own JSON responses (immune to the CSS churn that breaks DOM scrapers) to build a normalized
SQLite manifest, then hands explicit per-post URLs to **yt-dlp** (videos) and **gallery-dl**
(photo slideshows). See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and the verified
endpoint map.

> **v0.2 rewrite.** The original Selenium + hashed-CSS/XPath scraper rotted because those
> selectors change on every TikTok deploy. This version uses Playwright **response
> interception** instead. The old `src/` is gone; the *idea* (browser session → URL list →
> yt-dlp → sqlite) survives, the brittle code does not.

## Install

```sh
uv sync
uv run playwright install chromium   # only needed if you don't have Google Chrome installed
```

The tool prefers your **real installed Chrome** (`channel="chrome"`) for a weaker anti-bot
fingerprint; it falls back to Playwright's bundled Chromium if Chrome is absent.

## Usage

```sh
# 1. One-time login — opens Chrome with a dedicated tool profile. Log into TikTok, press Enter.
uv run tiktok-saver login

# 2. Read all your lists into the manifest (no downloads yet). Use YOUR username, no @.
uv run tiktok-saver enumerate _jdeck_ --surface all

# 3. Download the bytes for everything pending.
uv run tiktok-saver download _jdeck_ --surface all

# …or do enumerate + download in one pass:
uv run tiktok-saver run _jdeck_ --surface all

# See what's in the manifest and what state each post is in:
uv run tiktok-saver status _jdeck_
```

`--surface` accepts `all` (default), `collections`, `favorites`, or `liked`.
`--photos-only` / `--videos-only` restrict the download step. `--headless` runs Chrome
without a window (higher anti-bot risk — leave it off for the first runs).

Output (default `~/Downloads/TikTok-collections/`):

- `tt_manifest_<username>.db` — the SQLite manifest (posts, memberships, media, status)
- `videos/<id>.mp4` — downloaded videos + `.info.json` sidecars
- `photos/<id>/` — slideshow images (sibling of `videos/`)
- `cookies_<username>.txt` — session cookies for the download step (gitignored; keep private)

### Optional: cross-check against TikTok's official export

TikTok's in-app **Settings → Account → Download your data** (JSON) contains your Favorites
and Likes but **not** your Collections. If you request it, you can reconcile it against the
live-scraped manifest to catch anything the scroll missed:

```sh
uv run tiktok-saver reconcile _jdeck_ path/to/user_data.json
```

## What it will and won't get

- **Gets:** every video/photo in your Collections, Favorites and Likes that is still live
  and viewable by your logged-in account, with full metadata preserved even after link rot.
- **Can't get (recorded as terminal states, never retried):** deleted posts, private
  accounts that block you, region/age-locked videos. Their metadata is still captured from
  first sighting.

## Status

The pure-logic core (manifest, surface mapping, JSON parsing, error classification,
reconcile) is unit-tested (`uv run pytest`). The live browser flow needs your one-time
login to exercise end-to-end; the tab-opening selectors are the most likely thing to need
a small tweak after the first run against your account (see ARCHITECTURE.md → Empirical
unknowns).

## License

MIT.
