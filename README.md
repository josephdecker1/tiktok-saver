# TikTok Saver

Durable, first-party export of **your own** TikTok **Collections** (named folders),
**Favorites** (saved/bookmarks) and **Likes** — the list *and* the videos — plus optional
transcription, visual search and per-collection wiki pages over the archive.

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

Or install it on your PATH as a tool:

```sh
uv tool install --editable .         # `tiktok-saver` from anywhere; tracks your checkout
```

The tool prefers your **real installed Chrome** (`channel="chrome"`) for a weaker anti-bot
fingerprint; it falls back to Playwright's bundled Chromium if Chrome is absent.

## Usage

```sh
# 1. One-time login — opens Chrome with a dedicated tool profile. Log into TikTok, press Enter.
uv run tiktok-saver login

# 2. Read all your lists into the manifest (no downloads yet). Use YOUR username, no @.
uv run tiktok-saver enumerate yourname --surface all

# 3. Download the bytes for everything pending.
uv run tiktok-saver download yourname --surface all

# …or do enumerate + download in one pass:
uv run tiktok-saver run yourname --surface all

# See what's in the manifest and what state each post is in:
uv run tiktok-saver status yourname
```

`--surface` accepts `all` (default), `collections`, `favorites`, or `liked`.
`--photos-only` / `--videos-only` restrict the download step. `--headless` runs Chrome
without a window (higher anti-bot risk — leave it off for the first runs).

Output (default `~/Downloads/TikTok-collections/`, override with `--out`):

- `tt_manifest_<username>.db` — the SQLite manifest (posts, memberships, media, status)
- `videos/<id>.mp4` — downloaded videos + `.info.json` sidecars
- `photos/<id>/` — slideshow images (sibling of `videos/`)
- `cookies_<username>.txt` — session cookies for the download step (gitignored; keep private)

### Keeping it current

After the first full export, `sync` captures only what you saved since the last run
(newest-first watermark early-stop) and downloads it:

```sh
uv run tiktok-saver sync yourname --headless      # cron/nightly-friendly; exit 2 = session expired
```

### Optional: cross-check against TikTok's official export

TikTok's in-app **Settings → Account → Download your data** (JSON) contains your Favorites
and Likes but **not** your Collections. If you request it, you can reconcile it against the
live-scraped manifest to catch anything the scroll missed:

```sh
uv run tiktok-saver reconcile yourname path/to/user_data.json
```

## Search, transcripts and wiki (optional layers)

Each layer is independent; skip any you don't want.

### Transcription

`transcribe` sends downloaded videos (and slideshow audio) to a Whisper-style transcription
server you run, and stores the text in the manifest. Resumable: re-runs pick up exactly the
posts still missing.

```sh
export TIKTOK_TRANSCRIBE_ENDPOINT=http://your-server:8002
export TRANSCRIPTION_API_KEY=…                    # whatever your server expects in X-API-Key
uv run tiktok-saver transcribe yourname           # or pass --endpoint / --api-key-env
uv run tiktok-saver export-transcripts yourname   # per-post markdown for text indexing
```

**Server API contract** (any server implementing it works, e.g. a small
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) HTTP wrapper):

- `POST /transcribe` — multipart `file` upload, `X-API-Key` header; `video/mp4` accepted
  directly (server extracts audio). Response JSON:
  `{"text", "language", "language_probability", "duration"}`.
- `GET /health` — same auth header; response JSON must include `"model"` (recorded per post).

### Visual index + search

`index-frames` embeds video frames and slideshow images with **SigLIP 2** locally
(Apple-silicon MPS or CPU; needs the `[embed]` extra: `uv sync --extra embed`). `search`
blends max-over-frames visual similarity with transcript full-text hits ("spoken" marker):

```sh
uv run tiktok-saver index-frames yourname
uv run tiktok-saver search "sourdough starter"    # username optional with a single manifest
```

### Wiki pages

`wiki` compiles one markdown page per collection (plus one for uncollected favorites) from
captions + transcripts. Requires the [Claude Code CLI](https://claude.com/claude-code)
(`claude`) on your PATH; each page is one headless `claude -p` call with **all tools
disabled** (captions/transcripts are untrusted third-party text) from a project-free
working directory (so repo hooks can't interfere). Incremental: existing pages are skipped
unless `--force`.

```sh
uv run tiktok-saver wiki yourname                 # pages land in <out>/wiki/
```

## What it will and won't get

- **Gets:** every video/photo in your Collections, Favorites and Likes that is still live
  and viewable by your logged-in account, with full metadata preserved even after link rot.
- **Can't get (recorded as terminal states, never retried):** deleted posts, private
  accounts that block you, region/age-locked videos. Their metadata is still captured from
  first sighting.

## Status

The pure-logic core (manifest, surface mapping, JSON parsing, error classification,
reconcile, transcribe/index/wiki plumbing) is unit-tested (`uv run pytest`). The live
browser flow needs your one-time login to exercise end-to-end; the tab-opening selectors
are the most likely thing to need a small tweak after the first run against your account
(see ARCHITECTURE.md → Empirical unknowns).

## License

MIT — see [LICENSE](LICENSE).
