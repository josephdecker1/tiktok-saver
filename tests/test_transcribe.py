"""Transcribe: resume-by-construction, empty-text-is-done, transport failure
handling, and the box-down circuit breaker."""
import pytest

from tiktok_saver import transcribe
from tiktok_saver.manifest import Manifest


def _video_item(vid, unique="alice"):
    return {
        "id": vid,
        "desc": "cap",
        "createTime": 1700000000,
        "author": {"uniqueId": unique, "secUid": f"SEC_{unique}", "nickname": unique},
        "video": {"duration": 30},
        "stats": {},
    }


def _seed(tmp_path, vids=("111", "222", "333")):
    m = Manifest(tmp_path / "t.db")
    for vid in vids:
        m.upsert_post(_video_item(vid))
        f = tmp_path / f"{vid}.mp4"
        f.write_bytes(b"fake-video-bytes")
        m.add_media_file(vid, "video", 0, str(f), f.stat().st_size)
    m.commit()
    return m


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_network_preflight(monkeypatch):
    monkeypatch.setattr(transcribe, "preflight", lambda endpoint, key: "test-model")
    monkeypatch.setattr(transcribe.time, "sleep", lambda s: None)


def _ok_post(text="hello world"):
    def post(endpoint, key, path, timeout_s):
        return FakeResp(payload={
            "text": text, "language": "en",
            "language_probability": 0.99, "duration": 30.0})
    return post


def test_transcribes_pending_and_stores(tmp_path):
    m = _seed(tmp_path)
    tally = transcribe.transcribe_all(m, api_key="k", post_file=_ok_post())
    assert tally == {"transcribed": 3, "empty": 0, "errors": 0, "missing_file": 0}
    rows = dict(m.conn.execute("SELECT video_id, text FROM transcripts"))
    assert rows == {"111": "hello world", "222": "hello world", "333": "hello world"}
    model = m.conn.execute("SELECT DISTINCT model FROM transcripts").fetchone()[0]
    assert model == "test-model"


def test_rerun_skips_already_transcribed(tmp_path):
    m = _seed(tmp_path)
    m.set_transcript("111", "done already", "en", 0.9, 30.0, "test-model")
    m.commit()
    seen = []

    def post(endpoint, key, path, timeout_s):
        seen.append(path.stem)
        return FakeResp(payload={"text": "t", "language": "en",
                                 "language_probability": 0.9, "duration": 30.0})

    transcribe.transcribe_all(m, api_key="k", post_file=post)
    assert "111" not in seen and set(seen) == {"222", "333"}
    # The existing transcript was not clobbered.
    text = m.conn.execute(
        "SELECT text FROM transcripts WHERE video_id='111'").fetchone()[0]
    assert text == "done already"


def test_empty_text_is_stored_as_done(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    tally = transcribe.transcribe_all(m, api_key="k", post_file=_ok_post(text="  "))
    assert tally["empty"] == 1 and tally["transcribed"] == 0
    assert m.pending_transcriptions() == []  # done, never retried


def test_failed_post_leaves_post_pending(tmp_path):
    m = _seed(tmp_path, vids=("111",))

    def post(endpoint, key, path, timeout_s):
        return FakeResp(status_code=500, text="boom")

    tally = transcribe.transcribe_all(m, api_key="k", post_file=post)
    assert tally["errors"] == 1
    assert [r["video_id"] for r in m.pending_transcriptions()] == ["111"]


def test_4xx_does_not_retry(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    calls = []

    def post(endpoint, key, path, timeout_s):
        calls.append(1)
        return FakeResp(status_code=415, text="bad type")

    tally = transcribe.transcribe_all(m, api_key="k", post_file=post)
    assert len(calls) == 1 and tally["errors"] == 1


def test_box_down_circuit_breaker(tmp_path):
    m = _seed(tmp_path, vids=tuple(str(i) for i in range(100, 110)))

    def post(endpoint, key, path, timeout_s):
        raise ConnectionError("connection reset")

    with pytest.raises(transcribe.BoxDown):
        transcribe.transcribe_all(m, api_key="k", post_file=post)
    # Nothing was recorded as transcribed.
    assert m.transcript_counts()["transcribed"] == 0


def test_breaker_resets_on_success_between_failures(tmp_path):
    """CONSECUTIVE means consecutive: 2 files fail hard (4 transport errors),
    one succeeds, another fails hard (2 more). Cumulative would be 6 >= 5 and
    trip BoxDown; the reset on success must prevent that. (This is the exact
    mutation an adversarial review shipped green without this test.)"""
    m = _seed(tmp_path, vids=("100", "200", "300", "400"))

    def post(endpoint, key, path, timeout_s):
        if path.stem == "300":
            return FakeResp(payload={"text": "ok", "language": "en",
                                     "language_probability": 0.9, "duration": 30.0})
        raise ConnectionError("reset")

    tally = transcribe.transcribe_all(m, api_key="k", post_file=post)
    assert tally["transcribed"] == 1 and tally["errors"] == 3


def test_breaker_ignores_answered_5xx(tmp_path):
    """A box that ANSWERS 5xx is up (busy/OOM-guarding), not down — the
    breaker counts only transport failures, so 6 all-5xx files never trip it."""
    m = _seed(tmp_path, vids=tuple(str(i) for i in range(100, 106)))

    def post(endpoint, key, path, timeout_s):
        return FakeResp(status_code=503, text="busy")

    tally = transcribe.transcribe_all(m, api_key="k", post_file=post)
    assert tally["errors"] == 6           # all failed…
    assert m.transcript_counts()["transcribed"] == 0
    # …but no BoxDown raised: reaching here IS the assertion.


def test_code_bug_propagates_not_boxdown(tmp_path):
    """A non-OSError from the post path is a code bug and must surface as
    itself, never be absorbed into retry/box-down bookkeeping."""
    m = _seed(tmp_path, vids=("111",))

    def post(endpoint, key, path, timeout_s):
        raise TypeError("client bug")

    with pytest.raises(TypeError):
        transcribe.transcribe_all(m, api_key="k", post_file=post)


def test_missing_file_skipped(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    (tmp_path / "111.mp4").unlink()
    tally = transcribe.transcribe_all(m, api_key="k", post_file=_ok_post())
    assert tally["missing_file"] == 1
    # Still pending — a future run after a re-download picks it up.
    assert [r["video_id"] for r in m.pending_transcriptions()] == ["111"]


def test_no_api_key_raises(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    with pytest.raises(ValueError):
        transcribe.transcribe_all(m, api_key=None)


def test_pending_excludes_image_posts(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    item = _video_item("999", unique="bob")
    item.pop("video")
    item["imagePost"] = {"images": [{"imageURL": {"urlList": ["http://i/0.jpg"]}}]}
    m.upsert_post(item)
    img = tmp_path / "999_0.jpg"
    img.write_bytes(b"jpg")
    m.add_media_file("999", "image", 0, str(img), 3)
    m.commit()
    assert [r["video_id"] for r in m.pending_transcriptions()] == ["111"]


def test_transcript_export_rows_joins_collections(tmp_path):
    m = _seed(tmp_path, vids=("111",))
    m.add_membership("111", "collection", "c1", "bread", 0)
    m.add_membership("111", "collection", "c2", "food", 0)
    m.set_transcript("111", "how to score sourdough", "en", 0.9, 30.0, "m")
    m.set_transcript("222", "", "en", 0.9, 30.0, "m")  # empty -> excluded
    m.commit()
    rows = m.transcript_export_rows()
    assert len(rows) == 1
    assert rows[0]["text"] == "how to score sourdough"
    assert set(rows[0]["collections"].split(", ")) == {"bread", "food"}
