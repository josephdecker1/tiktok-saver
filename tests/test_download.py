"""Error classification into terminal vs retryable states, and output layout."""
from tiktok_saver.download import _classify, download_all
from tiktok_saver.manifest import Manifest


def test_private_login_walled():
    assert _classify("ERROR: Log in to view this video") == "private"
    assert _classify("This post is for friends only") == "private"
    assert _classify("followers-only content") == "private"


def test_gone_deleted():
    assert _classify("ERROR: Video not available") == "gone"
    assert _classify("This video has been deleted") == "gone"
    assert _classify("HTTP Error 404: Not Found") == "gone"


def test_region_locked():
    assert _classify("not available in your country") == "regionlocked"
    assert _classify("geo restricted") == "regionlocked"


def test_unknown_is_retryable_error():
    assert _classify("Connection reset by peer") == "error"
    assert _classify("") == "error"


def test_limit_caps_downloads(tmp_path, monkeypatch):
    import tiktok_saver.download as dl

    calls = []
    monkeypatch.setattr(dl, "_download_video",
                        lambda vid, *a, **k: (calls.append(vid), "done")[1])
    m = Manifest(tmp_path / "t.db")
    for v in ("a", "b", "c", "d", "e"):
        m.upsert_post({"id": v, "author": {"uniqueId": "u"}})
        m.ensure_status(v)
    m.commit()
    cookies = tmp_path / "c.txt"
    cookies.write_text("# empty\n")
    dl.download_all(m, tmp_path / "out", cookies, limit=2, log=lambda *_: None)
    assert len(calls) == 2          # only 2 of 5 pending fetched
    m.close()


def test_photos_only_filters_before_limit(tmp_path, monkeypatch):
    # Regression: --photos-only --limit N must yield N actual photos, not slice
    # N posts (that may be videos) and then skip them.
    import tiktok_saver.download as dl

    photo_calls, video_calls = [], []
    monkeypatch.setattr(dl, "_download_photo",
                        lambda vid, *a, **k: (photo_calls.append(vid), "done")[1])
    monkeypatch.setattr(dl, "_download_video",
                        lambda vid, *a, **k: (video_calls.append(vid), "done")[1])
    m = Manifest(tmp_path / "t.db")
    # 3 videos first, then 2 images — videos sort ahead in insertion order.
    for v in ("v1", "v2", "v3"):
        m.upsert_post({"id": v, "author": {"uniqueId": "u"}})
        m.ensure_status(v)
    for p in ("p1", "p2"):
        m.upsert_post({"id": p, "author": {"uniqueId": "u"},
                       "imagePost": {"images": []}})
        m.ensure_status(p)
    m.commit()
    cookies = tmp_path / "c.txt"
    cookies.write_text("# empty\n")
    dl.download_all(m, tmp_path / "out", cookies,
                    photos_only=True, limit=2, log=lambda *_: None)
    assert set(photo_calls) == {"p1", "p2"}     # both photos fetched
    assert video_calls == []                     # no videos, despite ordering
    m.close()


def test_videos_and_photos_are_siblings(tmp_path):
    # Empty manifest => no subprocess runs, but download_all must still create
    # videos/ and photos/ as SIBLINGS under the base out dir (not photos nested
    # under videos/).
    m = Manifest(tmp_path / "t.db")
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# empty\n")
    base = tmp_path / "TikTok-collections"
    download_all(m, base, cookies, log=lambda *_: None)
    assert (base / "videos").is_dir()
    assert (base / "photos").is_dir()
    assert not (base / "videos" / "photos").exists()   # no longer nested
    m.close()
