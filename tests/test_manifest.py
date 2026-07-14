"""Manifest: many-to-many membership, defensive flattening, idempotent re-run,
and terminal-state protection."""
import json

import pytest

from tiktok_saver.manifest import Manifest


def _video_item(vid="111", unique="alice", sec="SEC_alice"):
    return {
        "id": vid,
        "desc": "a caption",
        "createTime": 1700000000,
        "author": {"uniqueId": unique, "secUid": sec, "nickname": "Alice"},
        "music": {"id": "9", "title": "song", "authorName": "band"},
        "video": {"duration": 30, "cover": "http://c/cover.jpg"},
        "stats": {"playCount": 100, "diggCount": 5, "collectCount": 2, "commentCount": 1},
    }


def _photo_item(vid="222"):
    it = _video_item(vid=vid, unique="bob", sec="SEC_bob")
    it.pop("video")
    it["imagePost"] = {"images": [{"imageURL": {"urlList": ["http://i/0.jpg"]}}]}
    return it


def test_upsert_and_membership_many_to_many(tmp_path):
    m = Manifest(tmp_path / "t.db")
    vid = m.upsert_post(_video_item("111"))
    # Same video in two collections + liked => 3 membership rows, 1 post row.
    m.add_membership(vid, "collection", "c1", "Recipes", 0)
    m.add_membership(vid, "collection", "c2", "Workouts", 0)
    m.add_membership(vid, "liked", "_self", "liked", 0)
    m.commit()

    assert m.surface_counts() == {"collection": 1, "liked": 1}
    rows = list(m.conn.execute("SELECT COUNT(*) n FROM posts"))
    assert rows[0]["n"] == 1
    memb = list(m.conn.execute("SELECT COUNT(*) n FROM memberships"))
    assert memb[0]["n"] == 3


def test_post_type_routing(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("111"))
    m.upsert_post(_photo_item("222"))
    types = dict(m.conn.execute("SELECT video_id, post_type FROM posts"))
    assert types["111"] == "video"
    assert types["222"] == "image"


def test_canonical_url_uses_photo_for_images(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_photo_item("222"))
    url = list(m.conn.execute("SELECT canonical_url FROM posts WHERE video_id='222'"))[0][0]
    assert url == "https://www.tiktok.com/@bob/photo/222"


def test_defensive_flatten_missing_fields(tmp_path):
    m = Manifest(tmp_path / "t.db")
    # Minimal item — only an id. Must not raise; nulls degrade single columns.
    m.upsert_post({"id": "333"})
    row = list(m.conn.execute("SELECT * FROM posts WHERE video_id='333'"))[0]
    assert row["video_id"] == "333"
    assert row["author_unique_id"] is None
    # raw_json is preserved even for a sparse item.
    assert json.loads(row["raw_json"])["id"] == "333"


def test_upsert_missing_id_raises(tmp_path):
    m = Manifest(tmp_path / "t.db")
    with pytest.raises(ValueError):
        m.upsert_post({"desc": "no id here"})


def test_reenumeration_keeps_first_seen_updates_last_seen(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("111"))
    first = list(m.conn.execute("SELECT first_seen_ts, last_seen_ts FROM posts"))[0]
    # Re-upsert with a higher view count.
    it = _video_item("111")
    it["stats"]["playCount"] = 999
    m.upsert_post(it)
    after = list(m.conn.execute("SELECT first_seen_ts, view_count FROM posts"))[0]
    assert after["first_seen_ts"] == first["first_seen_ts"]
    assert after["view_count"] == 999


def test_ensure_status_never_resets_terminal(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("111"))
    m.ensure_status("111", "liked")
    m.set_status("111", "liked", "done")
    # A later re-enumeration calls ensure_status again — must stay 'done'.
    m.ensure_status("111", "liked")
    state = list(m.conn.execute("SELECT state FROM download_status WHERE video_id='111'"))[0][0]
    assert state == "done"


def test_pending_excludes_terminal_and_downloaded(tmp_path):
    m = Manifest(tmp_path / "t.db")
    for v in ("a", "b", "c", "d"):
        m.upsert_post(_video_item(v))
        m.ensure_status(v, "liked")
    m.set_status("a", "liked", "done")       # terminal
    m.set_status("b", "liked", "gone")       # terminal
    m.add_media_file("c", "video", 0, "/x/c.mp4", 10)  # has a file
    m.commit()
    pending = {r["video_id"] for r in m.pending_downloads()}
    assert pending == {"d"}


def test_mark_gone_transitions_unseen(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("old"))
    m.add_membership("old", "liked", "_self", "liked", 0)
    m.ensure_status("old", "liked")
    m.commit()
    # Simulate: a much later enumeration where 'old' was NOT seen.
    import time
    cutoff = int(time.time()) + 100
    n = m.mark_gone(cutoff, "liked")
    assert n == 1
    state = list(m.conn.execute("SELECT state FROM download_status WHERE video_id='old'"))[0][0]
    assert state == "gone"
