"""Download — fetch the bytes for enumerated posts.

Routing is by ``post_type`` from the manifest, NOT by a URL ``/photo/`` filter
(the old tool's blanket filter silently dropped every slideshow):

    video  -> yt-dlp   (default format = play_addr, watermark-free; never download_addr)
    image  -> gallery-dl (photos:true — yt-dlp only grabs a slideshow's audio)

Both CLIs are fed a Netscape cookies.txt exported from the logged-in Playwright
session (session.export_cookies_txt), so this step needs no live browser and no
fragile --cookies-from-browser profile parsing.

Failures are classified into the manifest's terminal states so a re-run never
retries something permanently gone:

    private       "login"/"private"/"friends only"/"followers"
    gone          "not available"/"deleted"/"video unavailable"/404
    regionlocked  "not available in your region"/geo
    error         anything else (transient — WILL retry next run)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .manifest import Manifest

# yt-dlp: default format is the watermark-free play_addr. We DON'T force a
# height cap here (TikTok verticals are already small); --no-warnings keeps the
# log readable. curl_cffi should be installed so impersonate has a target.
_YTDLP_BASE = [
    "yt-dlp",
    "--no-warnings",
    "--write-info-json",
    "--no-progress",
    "--retries", "3",
]

_GALLERY_DL_BASE = [
    "gallery-dl",
    "--option", "extractor.tiktok.photos=true",
]


def _classify(stderr: str) -> str:
    s = stderr.lower()
    if "region" in s or "not available in your country" in s or "geo" in s:
        return "regionlocked"
    if "log in" in s or "login" in s or "private" in s or "friends only" in s \
            or "followers" in s or "this post is" in s:
        return "private"
    if "not available" in s or "deleted" in s or "unavailable" in s \
            or "404" in s or "does not exist" in s or "removed" in s:
        return "gone"
    return "error"


def tools_available() -> dict[str, bool]:
    return {
        "yt-dlp": shutil.which("yt-dlp") is not None,
        "gallery-dl": shutil.which("gallery-dl") is not None,
    }


def download_all(
    manifest: Manifest,
    out_dir: str | Path,
    cookies_txt: str | Path,
    source_types: "str | list[str] | None" = None,
    photos_only: bool = False,
    videos_only: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Download every pending post. Returns a state->count tally of this run.

    ``out_dir`` is the base; videos land in ``out_dir/videos`` and photo
    slideshows in the SIBLING ``out_dir/photos`` (not nested under videos)."""
    out_dir = Path(out_dir)
    video_dir = out_dir / "videos"
    photo_dir = out_dir / "photos"
    video_dir.mkdir(parents=True, exist_ok=True)
    photo_dir.mkdir(parents=True, exist_ok=True)
    cookies_txt = str(cookies_txt)

    avail = tools_available()
    pending = manifest.pending_downloads(source_types)
    log(f"{len(pending)} post(s) pending download")

    tally: dict[str, int] = {}
    for row in pending:
        vid = row["video_id"]
        ptype = row["post_type"] or "video"
        url = row["canonical_url"]
        if url is None:
            manifest.set_status(vid, "error", error="no canonical url")
            tally["error"] = tally.get("error", 0) + 1
            continue
        if ptype == "image" and videos_only:
            continue
        if ptype == "video" and photos_only:
            continue

        if ptype == "image":
            if not avail["gallery-dl"]:
                log(f"  SKIP image {vid}: gallery-dl not installed")
                manifest.set_status(vid, "error", error="gallery-dl missing")
                continue
            state = _download_photo(vid, url, photo_dir, cookies_txt, manifest, log)
        else:
            if not avail["yt-dlp"]:
                log(f"  SKIP video {vid}: yt-dlp not installed")
                manifest.set_status(vid, "error", error="yt-dlp missing")
                continue
            state = _download_video(vid, url, video_dir, cookies_txt, manifest, log)

        manifest.set_status(vid, state)
        manifest.commit()
        tally[state] = tally.get(state, 0) + 1

    return tally


def _download_video(vid, url, out_dir, cookies_txt, manifest: Manifest, log) -> str:
    target = Path(out_dir) / "%(id)s.%(ext)s"
    cmd = _YTDLP_BASE + [
        "--cookies", cookies_txt,
        "-o", str(target),
        "--print", "after_move:filepath",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        path = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        size = Path(path).stat().st_size if path and Path(path).exists() else None
        manifest.add_media_file(vid, "video", 0, path, size)
        log(f"  ✓ video {vid}")
        return "done"
    state = _classify(proc.stderr)
    log(f"  ✗ video {vid}: {state} — {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}")
    return state


def _download_photo(vid, url, photo_dir, cookies_txt, manifest: Manifest, log) -> str:
    dest = Path(photo_dir) / vid                 # photo_dir is already <base>/photos
    cmd = _GALLERY_DL_BASE + [
        "--cookies", cookies_txt,
        "-D", str(dest),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        files = sorted(dest.glob("*")) if dest.exists() else []
        for num, f in enumerate(files):
            if f.is_file():
                manifest.add_media_file(vid, "image", num, str(f), f.stat().st_size)
        if files:
            log(f"  ✓ photo {vid} ({len(files)} image(s))")
            return "done"
        log(f"  ? photo {vid}: gallery-dl ok but no files")
        return "error"
    state = _classify(proc.stderr)
    log(f"  ✗ photo {vid}: {state}")
    return state
