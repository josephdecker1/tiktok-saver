"""SQLite manifest — decouples list MEMBERSHIP from the media bytes.

The old tool used one flat table with a single ``collection_id`` foreign key, so
a video saved in three collections could not be represented, and the ``raw_json``
that lets a delisted video keep its metadata was thrown away. This schema fixes
both:

    posts            one row per unique post; full raw JSON blob for link-rot insurance
    memberships      many-to-many: a post can be in N collections + favorites + liked
    media_files      one row per downloaded file (a slideshow => N image rows)
    download_status  per (post, source) download state machine; replaces failed_downloads.csv

All writes are UPSERTs so re-runs are idempotent.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    video_id        TEXT PRIMARY KEY,
    post_type       TEXT,                    -- 'video' | 'image'
    author_unique_id TEXT,
    author_sec_uid  TEXT,                    -- survives handle changes
    author_nickname TEXT,
    caption         TEXT,
    created_ts      INTEGER,
    music_id        TEXT,
    music_title     TEXT,
    music_author    TEXT,
    duration        INTEGER,
    cover_url       TEXT,
    canonical_url   TEXT,
    view_count      INTEGER,
    like_count      INTEGER,
    save_count      INTEGER,
    comment_count   INTEGER,
    raw_json        BLOB,                    -- full item JSON at first sighting
    first_seen_ts   INTEGER,
    last_seen_ts    INTEGER
);

CREATE TABLE IF NOT EXISTS memberships (
    video_id     TEXT,
    source_type  TEXT,                       -- 'collection' | 'favorites' | 'liked'
    source_id    TEXT,                       -- collectionId, or '_self' for favorites/liked
    source_name  TEXT,                       -- folder name, or the surface name
    position     INTEGER,                    -- first-seen order within ONE capture; NOT a
                                             -- global rank (incremental runs restart at 0),
                                             -- currently written but never read
    first_seen_ts INTEGER,
    PRIMARY KEY (video_id, source_type, source_id)
);

CREATE TABLE IF NOT EXISTS media_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT,
    kind          TEXT,                       -- 'video' | 'image' | 'audio' | 'cover'
    num           INTEGER,                    -- slideshow image index (0 for single)
    local_path    TEXT,
    sha256        TEXT,
    filesize      INTEGER,
    downloaded_ts INTEGER,
    UNIQUE (video_id, kind, num)
);

CREATE TABLE IF NOT EXISTS download_status (
    video_id       TEXT PRIMARY KEY,          -- download is a property of the POST, not
    state          TEXT,                      -- of any one list it appears in
    http_status    INTEGER,                   -- pending|done|gone|private|regionlocked|error
    error          TEXT,
    attempts       INTEGER DEFAULT 0,
    last_attempt_ts INTEGER
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id        TEXT PRIMARY KEY,          -- transcription is per-post, like download
    text            TEXT,                      -- '' is a valid result (music-only audio)
    language        TEXT,
    language_probability REAL,
    audio_duration  REAL,
    model           TEXT,                      -- e.g. 'faster-whisper-distil-large-v3'
    transcribed_ts  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_membership_video   ON memberships(video_id);
CREATE INDEX IF NOT EXISTS idx_membership_source  ON memberships(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_status_state       ON download_status(state);
"""

# Download states that must never be retried.
TERMINAL_STATES = ("done", "gone", "private", "regionlocked")


def _now() -> int:
    return int(time.time())


class Manifest:
    """Thin wrapper over a SQLite file. Not thread-safe; the download step uses
    a single writer connection and parallelizes only the network fetches."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- writes

    def upsert_post(self, item: dict[str, Any]) -> str:
        """Insert or refresh one post from a raw TikTok item-JSON dict.

        Flattens defensively: a missing field degrades one column, never the
        run. The whole item is kept in ``raw_json`` so nothing is lost if the
        shape drifts. Returns the video_id.
        """
        vid = str(item.get("id") or item.get("video_id") or "")
        if not vid:
            raise ValueError("item has no id")
        author = item.get("author") or {}
        music = item.get("music") or {}
        video = item.get("video") or {}
        stats = item.get("stats") or item.get("statsV2") or {}
        image_post = item.get("imagePost") or item.get("image_post")
        post_type = "image" if image_post else "video"

        author_unique = author.get("uniqueId") or author.get("unique_id")
        canonical = None
        if author_unique:
            kind = "photo" if post_type == "image" else "video"
            canonical = f"https://www.tiktok.com/@{author_unique}/{kind}/{vid}"

        cover = video.get("cover") or video.get("originCover")
        if not cover and image_post:
            imgs = image_post.get("images") or []
            if imgs:
                url_list = (imgs[0].get("imageURL") or {}).get("urlList") or []
                cover = url_list[0] if url_list else None

        row = {
            "video_id": vid,
            "post_type": post_type,
            "author_unique_id": author_unique,
            "author_sec_uid": author.get("secUid") or author.get("sec_uid"),
            "author_nickname": author.get("nickname"),
            "caption": item.get("desc"),
            "created_ts": _as_int(item.get("createTime") or item.get("create_time")),
            "music_id": str(music.get("id")) if music.get("id") is not None else None,
            "music_title": music.get("title"),
            "music_author": music.get("authorName") or music.get("author"),
            "duration": _as_int(video.get("duration") or music.get("duration")),
            "cover_url": cover,
            "canonical_url": canonical,
            "view_count": _as_int(stats.get("playCount") or stats.get("play_count")),
            "like_count": _as_int(stats.get("diggCount") or stats.get("digg_count")),
            "save_count": _as_int(stats.get("collectCount") or stats.get("collect_count")),
            "comment_count": _as_int(stats.get("commentCount") or stats.get("comment_count")),
            "raw_json": json.dumps(item, ensure_ascii=False),
            "seen": _now(),
        }
        self.conn.execute(
            """
            INSERT INTO posts (video_id, post_type, author_unique_id, author_sec_uid,
                author_nickname, caption, created_ts, music_id, music_title, music_author,
                duration, cover_url, canonical_url, view_count, like_count, save_count,
                comment_count, raw_json, first_seen_ts, last_seen_ts)
            VALUES (:video_id, :post_type, :author_unique_id, :author_sec_uid,
                :author_nickname, :caption, :created_ts, :music_id, :music_title,
                :music_author, :duration, :cover_url, :canonical_url, :view_count,
                :like_count, :save_count, :comment_count, :raw_json, :seen, :seen)
            ON CONFLICT(video_id) DO UPDATE SET
                last_seen_ts=:seen,
                -- refresh volatile fields but keep first_seen_ts and the original raw_json
                post_type=excluded.post_type,
                caption=COALESCE(excluded.caption, posts.caption),
                view_count=excluded.view_count,
                like_count=excluded.like_count,
                save_count=excluded.save_count,
                comment_count=excluded.comment_count,
                raw_json=excluded.raw_json
            """,
            row,
        )
        return vid

    def add_membership(
        self,
        video_id: str,
        source_type: str,
        source_id: str,
        source_name: str,
        position: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO memberships (video_id, source_type, source_id, source_name,
                position, first_seen_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, source_type, source_id) DO UPDATE SET
                source_name=excluded.source_name
            """,
            (video_id, source_type, source_id, source_name, position, _now()),
        )

    def ensure_status(self, video_id: str) -> None:
        """Create a pending download_status row if none exists (never resets a
        terminal state on re-enumeration)."""
        self.conn.execute(
            """
            INSERT INTO download_status (video_id, state, last_attempt_ts)
            VALUES (?, 'pending', NULL)
            ON CONFLICT(video_id) DO NOTHING
            """,
            (video_id,),
        )

    def set_status(
        self,
        video_id: str,
        state: str,
        http_status: int | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO download_status (video_id, state, http_status,
                error, attempts, last_attempt_ts)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                state=excluded.state,
                http_status=excluded.http_status,
                error=excluded.error,
                attempts=download_status.attempts + 1,
                last_attempt_ts=excluded.last_attempt_ts
            """,
            (video_id, state, http_status, error, _now()),
        )

    def add_media_file(
        self,
        video_id: str,
        kind: str,
        num: int,
        local_path: str,
        filesize: int | None,
        sha256: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO media_files (video_id, kind, num, local_path, sha256,
                filesize, downloaded_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, kind, num) DO UPDATE SET
                local_path=excluded.local_path, filesize=excluded.filesize,
                sha256=excluded.sha256, downloaded_ts=excluded.downloaded_ts
            """,
            (video_id, kind, num, local_path, sha256, filesize, _now()),
        )

    def set_transcript(
        self,
        video_id: str,
        text: str,
        language: str | None,
        language_probability: float | None,
        audio_duration: float | None,
        model: str | None,
    ) -> None:
        """Record a transcription result. Empty text is stored (it means the
        audio carried no recognizable speech) so the post is done, not retried."""
        self.conn.execute(
            """
            INSERT INTO transcripts (video_id, text, language, language_probability,
                audio_duration, model, transcribed_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                text=excluded.text, language=excluded.language,
                language_probability=excluded.language_probability,
                audio_duration=excluded.audio_duration, model=excluded.model,
                transcribed_ts=excluded.transcribed_ts
            """,
            (video_id, text, language, language_probability, audio_duration, model, _now()),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ----------------------------------------------------------------- reads

    def pending_downloads(
        self, source_types: "str | list[str] | None" = None
    ) -> list[sqlite3.Row]:
        """Posts needing a download: no media file yet AND not in a terminal
        state. ONE row per post (download is per-post, not per-list), so a video
        in several lists is fetched once, never N times. ``source_types``
        restricts to posts that are MEMBERS of any of those surfaces, without
        multiplying rows."""
        q = """
            SELECT p.video_id, p.post_type, p.canonical_url,
                   p.author_unique_id, d.state
            FROM download_status d
            JOIN posts p ON p.video_id = d.video_id
            WHERE d.state NOT IN ('done','gone','private','regionlocked')
              AND NOT EXISTS (SELECT 1 FROM media_files mf WHERE mf.video_id = p.video_id)
        """
        args: tuple = ()
        if source_types:
            if isinstance(source_types, str):
                source_types = [source_types]
            placeholders = ",".join("?" * len(source_types))
            q += (f" AND EXISTS (SELECT 1 FROM memberships m WHERE "
                  f"m.video_id = p.video_id AND m.source_type IN ({placeholders}))")
            args = tuple(source_types)
        return list(self.conn.execute(q, args))

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) n FROM download_status GROUP BY state"
        )
        return {r["state"]: r["n"] for r in rows}

    def surface_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT source_type, COUNT(DISTINCT video_id) n FROM memberships GROUP BY source_type"
        )
        return {r["source_type"]: r["n"] for r in rows}

    def collection_names(self) -> list[tuple[str, str, int]]:
        rows = self.conn.execute(
            """
            SELECT source_id, source_name, COUNT(DISTINCT video_id) n
            FROM memberships WHERE source_type='collection'
            GROUP BY source_id, source_name ORDER BY source_name
            """
        )
        return [(r["source_id"], r["source_name"], r["n"]) for r in rows]

    def known_video_ids(
        self, source_type: str, source_id: str | None = None
    ) -> set[str]:
        """Video ids already recorded for a surface (the incremental watermark
        source). Pass ``source_id`` to scope to one collection folder."""
        q = "SELECT DISTINCT video_id FROM memberships WHERE source_type=?"
        args: list = [source_type]
        if source_id is not None:
            q += " AND source_id=?"
            args.append(source_id)
        return {r["video_id"] for r in self.conn.execute(q, tuple(args))}

    def pending_transcriptions(self, limit: int | None = None) -> list[sqlite3.Row]:
        """Downloaded videos with no transcript row yet. One row per post
        (media_files is UNIQUE(video_id, kind, num) and videos are single-file),
        oldest saves first so a resumed backfill stays deterministic."""
        q = """
            SELECT p.video_id, p.author_unique_id, p.duration, mf.local_path
            FROM media_files mf
            JOIN posts p ON p.video_id = mf.video_id
            LEFT JOIN transcripts t ON t.video_id = mf.video_id
            WHERE mf.kind = 'video' AND t.video_id IS NULL
            ORDER BY p.first_seen_ts, p.video_id
        """
        args: tuple = ()
        if limit is not None:
            q += " LIMIT ?"
            args = (limit,)
        return list(self.conn.execute(q, args))

    def transcript_counts(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) n, SUM(text = '') empty FROM transcripts"
        ).fetchone()
        return {"transcribed": row["n"] or 0, "empty": row["empty"] or 0}

    def all_canonical_urls(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT canonical_url FROM posts WHERE canonical_url IS NOT NULL"
        )
        return {r["canonical_url"] for r in rows}


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
