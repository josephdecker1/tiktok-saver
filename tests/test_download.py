"""Error classification into terminal vs retryable states."""
from tiktok_saver.download import _classify


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
