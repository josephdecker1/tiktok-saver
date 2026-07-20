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
