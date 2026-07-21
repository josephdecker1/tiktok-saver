"""Transcribe downloaded videos via a Whisper transcription server.

Expected server API (README.md → Transcription documents the same contract):
``POST /transcribe`` — multipart ``file`` upload authenticated with
``X-API-Key``; ``video/mp4`` is accepted directly (the server extracts audio
itself). Response: ``{"text", "language", "language_probability",
"duration"}``. ``GET /health`` (same auth header) must return JSON including
a ``model`` field — used as the preflight and to record which model
transcribed each post.

The runner is deliberately serial — typical single-GPU servers serialize work
anyway, so client-side concurrency would only hold connections open. Results
go into the manifest's ``transcripts`` table keyed by post, which also makes
the run resumable by construction: a re-run picks up exactly the posts
without a transcript row.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from curl_cffi import CurlMime, requests

from .manifest import Manifest

ENDPOINT_ENV = "TIKTOK_TRANSCRIBE_ENDPOINT"
API_KEY_ENV = "TRANSCRIPTION_API_KEY"

# Give the box 3x realtime plus queue headroom per request. The measured rate is
# far faster (~13s of GPU per audio-minute), but another LAN client may hold the
# server's single slot; the floor absorbs short waits without hanging a batch.
TIMEOUT_FLOOR_S = 180
TIMEOUT_PER_AUDIO_S = 3.0

# A run aborts after this many CONSECUTIVE transport failures — that is the
# server-down signature (host dead, port-forward stale), not per-file bad luck.
MAX_CONSECUTIVE_FAILURES = 5


class BoxDown(RuntimeError):
    """The transcription server stopped answering; the batch should stop, not spin."""


# The box checks Content-Type against its allowlist; both these and the
# application/octet-stream fallback are in the server's default set.
_CONTENT_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".m4a": "audio/x-m4a", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
}


def _post_file(
    endpoint: str, api_key: str, path: Path, timeout_s: float
) -> requests.Response:
    # curl_cffi has no requests-style `files=`; its multipart API streams from
    # local_path without loading the file into memory.
    mp = CurlMime()
    mp.addpart(name="file",
               content_type=_CONTENT_TYPES.get(
                   path.suffix.lower(), "application/octet-stream"),
               filename=path.name, local_path=str(path))
    try:
        return requests.post(
            f"{endpoint}/transcribe",
            headers={"X-API-Key": api_key},
            params={"word_timestamps": "false"},
            multipart=mp,
            timeout=timeout_s,
        )
    finally:
        mp.close()


def preflight(endpoint: str, api_key: str) -> str:
    """Authenticated /health: fail fast on a dead box or bad key, and return
    the box's live model name (the /transcribe response doesn't carry it)."""
    resp = requests.get(
        f"{endpoint}/health", headers={"X-API-Key": api_key}, timeout=10
    )
    if resp.status_code != 200:
        raise RuntimeError(f"/health returned HTTP {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    model = payload.get("model")
    if not model:
        # Minimal payload = the key was not accepted (unauthenticated /health).
        raise RuntimeError("/health returned the unauthenticated payload — bad API key?")
    return model


def transcribe_all(
    manifest: Manifest,
    endpoint: str,
    api_key: str | None = None,
    limit: int | None = None,
    post_file: Callable = _post_file,
    progress: Callable[[str], None] = print,
) -> dict[str, int]:
    """Send every pending video to the server; store results; return a tally.

    Per file: one retry on a transport error or 5xx, then the post is left
    pending (a later run retries it). Empty transcript text is a RESULT
    (music-only audio) and is stored, so the post never re-runs.
    """
    if not endpoint:
        raise ValueError(f"no endpoint — set ${ENDPOINT_ENV} or pass --endpoint")
    if not api_key:
        raise ValueError(f"no API key — set ${API_KEY_ENV} or pass --api-key-env")

    box_model = preflight(endpoint, api_key)
    progress(f"transcribe: box ok — model {box_model}")

    pending = manifest.pending_transcriptions(limit=limit)
    tally = {"transcribed": 0, "empty": 0, "no_audio": 0, "errors": 0,
             "missing_file": 0}
    consecutive_failures = 0
    total = len(pending)
    progress(f"transcribe: {total} video(s) pending")

    for i, row in enumerate(pending, 1):
        path = Path(row["local_path"])
        if not path.is_file():
            tally["missing_file"] += 1
            progress(f"  [{i}/{total}] {row['video_id']}: missing file {path} — skipped")
            continue

        timeout_s = max(TIMEOUT_FLOOR_S, (row["duration"] or 0) * TIMEOUT_PER_AUDIO_S)
        result = None
        error: str | None = None
        for attempt in (1, 2):
            last_attempt = attempt == 2
            try:
                # OSError covers every curl_cffi transport error (its
                # RequestException subclasses OSError) plus local file I/O;
                # anything else is a code bug and must propagate, not be
                # mislabeled "box down".
                resp = post_file(endpoint, api_key, path, timeout_s)
            except OSError as e:
                error = f"{type(e).__name__}: {e}"
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    tally["errors"] += 1
                    raise BoxDown(
                        f"{consecutive_failures} consecutive transport failures "
                        f"(last: {error}) — box down? Batch stopped; re-run resumes here."
                    )
                if not last_attempt:
                    time.sleep(2)
                continue
            # The box ANSWERED — whatever the status, it is up, not down.
            consecutive_failures = 0
            if resp.status_code >= 500:
                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if not last_attempt:
                    time.sleep(5)  # 503 = busy/memory guard; brief backoff
                continue
            if resp.status_code == 400 and "Audio could not be processed" in resp.text:
                # Silent video (no audio stream): the server's whisper has
                # nothing to chew. That is a RESULT — no speech — not an error;
                # record it as an empty transcript so the post is done forever.
                manifest.set_transcript(
                    row["video_id"], text="", language=None,
                    language_probability=None, audio_duration=None,
                    model=box_model)
                manifest.commit()
                tally["no_audio"] += 1
                result = "no_audio"
                break
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break  # 4xx won't improve on retry
            result = resp.json()
            break

        if result is None:
            tally["errors"] += 1
            progress(f"  [{i}/{total}] {row['video_id']}: FAILED ({error}) — left pending")
            continue
        if result == "no_audio":
            continue

        text = (result.get("text") or "").strip()
        manifest.set_transcript(
            row["video_id"],
            text=text,
            language=result.get("language"),
            language_probability=result.get("language_probability"),
            audio_duration=result.get("duration"),
            model=box_model,
        )
        manifest.commit()  # per-file: a killed batch keeps everything finished
        if text:
            tally["transcribed"] += 1
        else:
            tally["empty"] += 1
        if i % 25 == 0 or i == total:
            progress(f"  [{i}/{total}] done={tally['transcribed']} "
                     f"empty={tally['empty']} errors={tally['errors']}")

    return tally


def resolve_api_key(env_var: str = API_KEY_ENV) -> str | None:
    return os.environ.get(env_var) or None
