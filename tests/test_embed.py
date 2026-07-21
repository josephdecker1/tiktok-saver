"""Visual index: adaptive sampling math, ffmpeg command shape, per-post resume
markers, f16 round-trip, and max-over-frames retrieval (never mean-pooling)."""
import numpy as np
import pytest

from tiktok_saver import embed, frames
from tiktok_saver.manifest import Manifest


# ----------------------------------------------------------------- frames

def test_sample_fps_short_video_uses_interval():
    assert frames.sample_fps(30) == pytest.approx(0.5)      # 1 frame / 2s


def test_sample_fps_long_video_caps_frames():
    fps = frames.sample_fps(2080)
    assert fps * 2080 == pytest.approx(frames.MAX_FRAMES_PER_VIDEO)


def test_sample_fps_missing_duration_defaults():
    assert frames.sample_fps(None) == pytest.approx(0.5)


def test_ffmpeg_cmd_shape(tmp_path):
    cmd = frames.ffmpeg_cmd(tmp_path / "v.mp4", tmp_path / "f_%04d.jpg", 0.5)
    assert cmd[0] == "ffmpeg"
    assert f"fps=0.500000,scale=-2:{frames.FRAME_HEIGHT}" in cmd
    assert str(frames.MAX_FRAMES_PER_VIDEO) in cmd


def test_extract_frames_timestamps(tmp_path):
    def fake_run(cmd, **kw):
        for n in (1, 2, 3):
            (tmp_path / f"f_{n:04d}.jpg").write_bytes(b"jpg")
        class R: returncode = 0; stderr = ""
        return R()

    out = frames.extract_frames(tmp_path / "v.mp4", 6.0, workdir=tmp_path, run=fake_run)
    assert [ts for ts, _ in out] == [0.0, 2.0, 4.0]   # n/fps at fps=0.5


# ----------------------------------------------------------------- fixtures

def _video_item(vid, unique="alice", duration=30):
    return {
        "id": vid, "desc": f"caption {vid}", "createTime": 1700000000,
        "author": {"uniqueId": unique, "secUid": f"S_{unique}", "nickname": unique},
        "video": {"duration": duration}, "stats": {},
    }


def _seed_video(m, tmp_path, vid):
    m.upsert_post(_video_item(vid))
    f = tmp_path / f"{vid}.mp4"
    f.write_bytes(b"vid")
    m.add_media_file(vid, "video", 0, str(f), 3)


def _seed_slideshow(m, tmp_path, vid, n_slides=3):
    item = _video_item(vid, unique="bob")
    item.pop("video")
    item["imagePost"] = {"images": [{"imageURL": {"urlList": ["http://i/0.jpg"]}}]}
    m.upsert_post(item)
    for n in range(n_slides):
        f = tmp_path / f"{vid}_{n}.jpg"
        f.write_bytes(b"jpg")
        m.add_media_file(vid, "image", n, str(f), 3)


class FakeEmbedder:
    """Deterministic unit vectors; axis chosen by filename hash."""
    model_id = "fake-model"

    def embed_images(self, paths):
        out = np.zeros((len(paths), 8), dtype=np.float32)
        for i, p in enumerate(paths):
            out[i, hash(p.name) % 8] = 1.0
        return out


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """Stub extraction for indexing tests only — NOT autouse, because embed.fr
    IS the frames module and patching it would blind the frames tests above."""
    def fake_extract(video_path, duration, workdir=None, run=None):
        return [(0.0, video_path), (2.0, video_path)]   # 2 "frames" per video
    monkeypatch.setattr(embed.fr, "extract_frames", fake_extract)


# ----------------------------------------------------------------- indexing

def test_index_videos_and_slides_and_resume(tmp_path, no_ffmpeg):
    m = Manifest(tmp_path / "t.db")
    _seed_video(m, tmp_path, "111")
    _seed_slideshow(m, tmp_path, "222", n_slides=3)
    m.commit()

    tally = embed.index_all(m, embedder=FakeEmbedder())
    assert tally == {"posts": 2, "vectors": 5, "errors": 0, "missing_file": 0}
    assert m.visual_index_counts() == {"posts": 2, "vectors": 5}

    # Re-run: nothing pending — resume marker works.
    tally2 = embed.index_all(m, embedder=FakeEmbedder())
    assert tally2["posts"] == 0 and m.visual_index_counts()["vectors"] == 5


def test_index_limit_counts_posts_not_files(tmp_path):
    m = Manifest(tmp_path / "t.db")
    _seed_slideshow(m, tmp_path, "222", n_slides=3)
    _seed_video(m, tmp_path, "111")
    # Force a deterministic order (same-second seeds tie-break by video_id):
    # the slideshow is the OLDER save, so limit=1 must keep all 3 of its files.
    m.conn.execute("UPDATE posts SET first_seen_ts=100 WHERE video_id='222'")
    m.conn.execute("UPDATE posts SET first_seen_ts=200 WHERE video_id='111'")
    m.commit()
    rows = m.pending_visual_index(limit=1)
    assert {r["video_id"] for r in rows} == {"222"}     # whole slideshow, one post
    assert len(rows) == 3


def test_failed_post_left_pending(tmp_path, no_ffmpeg):
    m = Manifest(tmp_path / "t.db")
    _seed_video(m, tmp_path, "111")
    m.commit()

    class Boom(FakeEmbedder):
        def embed_images(self, paths):
            raise ValueError("mps exploded")

    tally = embed.index_all(m, embedder=Boom())
    assert tally["errors"] == 1
    assert {r["video_id"] for r in m.pending_visual_index()} == {"111"}


# ----------------------------------------------------------------- search

def test_search_max_over_frames_not_mean(tmp_path):
    """A post with ONE great frame must outrank a post whose frames are all
    mediocre — the exact property mean-pooling destroys."""
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("one_hit", unique="alice"))
    m.upsert_post(_video_item("all_meh", unique="bob"))
    q = np.zeros(8, dtype=np.float32); q[0] = 1.0

    hit = np.zeros(8, dtype=np.float16); hit[0] = 1.0        # cos = 1.0
    miss = np.zeros(8, dtype=np.float16); miss[1] = 1.0      # cos = 0.0
    meh = np.full(8, 0.35355, dtype=np.float16)              # cos ≈ 0.35

    m.store_frame_vector("one_hit", "frame", 0.0, miss.tobytes())
    m.store_frame_vector("one_hit", "frame", 2.0, hit.tobytes())
    m.store_frame_vector("one_hit", "frame", 4.0, miss.tobytes())
    for ts in (0.0, 2.0, 4.0):
        m.store_frame_vector("all_meh", "frame", ts, meh.tobytes())
    m.commit()

    hits = embed.search(m, "anything", k=2, query_vec=q)
    assert [h["video_id"] for h in hits] == ["one_hit", "all_meh"]
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-3)
    assert hits[0]["match_ts"] == 2.0                        # the matching frame
    # mean of one_hit (1/3) would have LOST to all_meh (0.35) — max must win.


def test_search_joins_caption_and_transcript(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("111"))
    m.set_transcript("111", "the spoken words", "en", 0.9, 30.0, "m")
    v = np.zeros(8, dtype=np.float16); v[0] = 1.0
    m.store_frame_vector("111", "frame", 0.0, v.tobytes())
    m.commit()
    q = np.zeros(8, dtype=np.float32); q[0] = 1.0
    (hit,) = embed.search(m, "x", k=1, query_vec=q)
    assert hit["caption"] == "caption 111"
    assert hit["transcript_snippet"] == "the spoken words"


def test_search_empty_index(tmp_path):
    m = Manifest(tmp_path / "t.db")
    assert embed.search(m, "x", k=5, query_vec=np.zeros(8)) == []


def test_search_fts_bonus_exact(tmp_path):
    """A spoken-but-not-shown query surfaces the post from `search`: the FTS
    bonus lifts it above a visually-closer competitor, and the flag is set."""
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("spoken", unique="alice"))
    m.upsert_post(_video_item("visual", unique="bob"))
    m.set_transcript("spoken", "we cover quantum entanglement here", "en", 0.9, 30.0, "m")
    q = np.zeros(8, dtype=np.float32); q[0] = 1.0
    tiny = np.zeros(8, dtype=np.float16); tiny[0] = 0.1      # cos 0.1
    mid = np.zeros(8, dtype=np.float16); mid[0] = 0.25       # cos 0.25
    m.store_frame_vector("spoken", "frame", 0.0, tiny.tobytes())
    m.store_frame_vector("visual", "frame", 0.0, mid.tobytes())
    m.commit()

    hits = embed.search(m, "quantum entanglement", k=2, query_vec=q)
    # spoken: 0.1 + 0.2 bonus = 0.3 beats visual 0.25; flag set; no-hit unflagged
    assert [h["video_id"] for h in hits] == ["spoken", "visual"]
    assert hits[0]["transcript_hit"] is True
    assert hits[0]["score"] == pytest.approx(0.1 + embed.FTS_BLEND_BONUS, abs=2e-3)
    assert hits[1]["transcript_hit"] is False


def test_fts_self_heals_preexisting_transcripts(tmp_path):
    """Transcripts written before the FTS table existed are indexed on first
    search (the live archive's 1,701 rows are exactly this case)."""
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_video_item("111"))
    m.set_transcript("111", "sourdough scoring technique", "en", 0.9, 30.0, "m")
    m.conn.execute("DELETE FROM transcripts_fts")            # simulate pre-FTS data
    m.commit()
    assert m.transcript_fts_matches("sourdough") == {} or True  # trigger sync
    assert "111" in m.transcript_fts_matches("sourdough")


def test_fts_weird_query_never_raises(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.set_transcript("111", "text", "en", 0.9, 30.0, "m")
    m.commit()
    assert m.transcript_fts_matches('AND OR NOT ( " * :') == {} or True
    assert isinstance(m.transcript_fts_matches("(((("), dict)


def test_index_cleans_tempdir_even_on_failure(tmp_path, monkeypatch):
    """The per-post failure path must not leak the frames tempdir."""
    import tempfile as _tf
    made: list = []
    real_mkdtemp = _tf.mkdtemp

    def tracking_mkdtemp(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        made.append(d)
        return d

    monkeypatch.setattr(embed.tempfile if hasattr(embed, "tempfile") else _tf,
                        "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(embed.fr, "extract_frames",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ffmpeg boom")))
    m = Manifest(tmp_path / "t.db")
    _seed_video(m, tmp_path, "111")
    m.commit()
    tally = embed.index_all(m, embedder=FakeEmbedder())
    assert tally["errors"] == 1
    import os
    assert all(not os.path.isdir(d) for d in made)


def test_extract_frames_colorspace_remux_fallback(tmp_path):
    """First ffmpeg call fails with 'Invalid color space' -> remux via
    hevc_metadata bsf -> extraction from the fixed copy succeeds."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        r = R()
        if len(calls) == 1:                       # original extract fails
            r.returncode = 1
            r.stderr = "graph -1 input from stream 0:0: Invalid color space"
        elif len(calls) == 2:                     # remux succeeds
            assert "hevc_metadata" in " ".join(map(str, cmd))
        else:                                     # extract from fixed copy
            (tmp_path / "f_0001.jpg").write_bytes(b"jpg")
        return r

    out = frames.extract_frames(tmp_path / "v.mp4", 4.0, workdir=tmp_path, run=fake_run)
    assert len(calls) == 3
    assert [ts for ts, _ in out] == [0.0]
