"""Reconcile against a fixture shaped like TikTok's real user_data.json export."""
import json

from tiktok_saver.manifest import Manifest
from tiktok_saver.reconcile import reconcile


def _export():
    return {
        "Activity": {
            "Favorite Videos": {
                "FavoriteVideoList": [
                    {"Date": "2026-01-01", "Link": "https://www.tiktokv.com/share/video/111/"},
                    {"Date": "2026-01-02", "Link": "https://www.tiktokv.com/share/video/999/"},
                ]
            },
            "Like List": {
                "ItemFavoriteList": [
                    {"Date": "2026-01-03", "Link": "https://www.tiktokv.com/share/video/222/"},
                ]
            },
        }
    }


def test_reconcile_flags_missing(tmp_path):
    m = Manifest(tmp_path / "t.db")
    # Manifest has 111 and 222 but NOT 999.
    m.upsert_post({"id": "111", "author": {"uniqueId": "a"}})
    m.upsert_post({"id": "222", "author": {"uniqueId": "b"}})
    m.commit()
    exp = tmp_path / "user_data.json"
    exp.write_text(json.dumps(_export()), encoding="utf-8")

    result = reconcile(m, exp, log=lambda *_: None)
    assert result["favorites_missing"] == ["https://www.tiktokv.com/share/video/999/"]
    assert result["liked_missing"] == []


def test_reconcile_empty_manifest_flags_all(tmp_path):
    m = Manifest(tmp_path / "t.db")
    exp = tmp_path / "user_data.json"
    exp.write_text(json.dumps(_export()), encoding="utf-8")
    result = reconcile(m, exp, log=lambda *_: None)
    assert len(result["favorites_missing"]) == 2
    assert len(result["liked_missing"]) == 1
