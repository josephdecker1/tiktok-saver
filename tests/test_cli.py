"""CLI helpers — currently the search-username inference."""
from pathlib import Path

from tiktok_saver.cli import _infer_username


def test_infer_username_single_manifest(tmp_path):
    (tmp_path / "tt_manifest_alice.db").touch()
    assert _infer_username(tmp_path) == "alice"


def test_infer_username_preserves_underscored_names(tmp_path):
    (tmp_path / "tt_manifest__jdeck_.db").touch()
    assert _infer_username(tmp_path) == "_jdeck_"


def test_infer_username_none_when_empty(tmp_path):
    assert _infer_username(tmp_path) is None


def test_infer_username_none_when_ambiguous(tmp_path):
    (tmp_path / "tt_manifest_alice.db").touch()
    (tmp_path / "tt_manifest_bob.db").touch()
    assert _infer_username(tmp_path) is None
