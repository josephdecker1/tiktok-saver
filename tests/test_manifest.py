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
    m.ensure_status("111")
    m.set_status("111", "done")
    # A later re-enumeration calls ensure_status again — must stay 'done'.
    m.ensure_status("111")
    state = list(m.conn.execute("SELECT state FROM download_status WHERE video_id='111'"))[0][0]
    assert state == "done"


def test_pending_excludes_terminal_and_downloaded(tmp_path):
    m = Manifest(tmp_path / "t.db")
    for v in ("a", "b", "c", "d"):
        m.upsert_post(_video_item(v))
        m.ensure_status(v)
    m.set_status("a", "done")       # terminal
    m.set_status("b", "gone")       # terminal
    m.add_media_file("c", "video", 0, "/x/c.mp4", 10)  # has a file
    m.commit()
    pending = {r["video_id"] for r in m.pending_downloads()}
    assert pending == {"d"}


def test_multi_surface_video_pending_once(tmp_path):
    # A video saved AND liked must appear ONCE in the pending snapshot, not
    # twice — the HIGH bug Saruman caught (2-3x re-download + path clobber).
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("dup"))
    m.add_membership("dup", "favorites", "_self", "favorites", 0)
    m.add_membership("dup", "liked", "_self", "liked", 0)
    m.ensure_status("dup")
    m.commit()
    rows = m.pending_downloads()
    assert [r["video_id"] for r in rows] == ["dup"]     # exactly one row


def test_pending_source_filter_by_membership(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("liked_only"))
    m.add_membership("liked_only", "liked", "_self", "liked", 0)
    m.ensure_status("liked_only")
    m.upsert_post(_video_item("fav_only"))
    m.add_membership("fav_only", "favorites", "_self", "favorites", 0)
    m.ensure_status("fav_only")
    m.commit()
    liked = {r["video_id"] for r in m.pending_downloads("liked")}
    assert liked == {"liked_only"}


def test_pending_source_filter_accepts_list(tmp_path):
    m = Manifest(tmp_path / "t.db")
    for v, st in (("c", "collection"), ("f", "favorites"), ("l", "liked")):
        m.upsert_post(_video_item(v))
        m.add_membership(v, st, "_self", st, 0)
        m.ensure_status(v)
    m.commit()
    got = {r["video_id"] for r in m.pending_downloads(["collection", "favorites"])}
    assert got == {"c", "f"}      # no liked
