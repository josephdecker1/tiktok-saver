"""Visual index: SigLIP 2 embeddings for video frames and slideshow images.

Model: google/siglip2-so400m-patch16-384 (Apache-2.0) — a dual encoder, so a
text query embeds into the same space as the frames and retrieval is a cosine
top-k. Runs on Apple-silicon MPS (fp16), CPU fallback.

Storage is deliberately boring: L2-normalized float16 BLOBs in the manifest
(``frame_vectors``), brute-force scored with numpy at query time — at ~60k
vectors x 1152 dims that is ~130 MB and a few ms per query; an ANN index would
be pure overhead. Retrieval groups per post by MAX frame score (late
interaction), never by mean-pooling — averaging frames is where video
embedding quality goes to die.

The heavy deps (torch/transformers) are the ``embed`` extra; everything here
imports them lazily so the rest of the CLI works without them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from . import frames as fr
from .manifest import Manifest

MODEL_ID = "google/siglip2-so400m-patch16-384"
EMBED_DIM = 1152
DEFAULT_BATCH = 16


def _require_numpy():
    try:
        import numpy as np
        return np
    except ImportError as e:
        raise RuntimeError(
            "numpy missing — install the embed extra: "
            "uv tool install --reinstall --editable '<repo-dir>[embed]'"
        ) from e


class Embedder:
    """Lazy-loaded SigLIP 2 wrapper. Instantiating loads the model (slow once:
    ~4.5 GB of weights on first ever run, then local cache)."""

    def __init__(self, model_id: str = MODEL_ID, device: str | None = None):
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as e:
            raise RuntimeError(
                "torch/transformers missing — install the embed extra: "
                "uv tool install --reinstall --editable '<repo-dir>[embed]'"
            ) from e
        self.torch = torch
        self.np = _require_numpy()
        self.device = device or (
            "mps" if torch.backends.mps.is_available() else "cpu")
        dtype = torch.float16 if self.device == "mps" else torch.float32
        self.model = AutoModel.from_pretrained(
            model_id, torch_dtype=dtype).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model_id = model_id

    def embed_images(self, paths: list[Path]):
        """(n, EMBED_DIM) float32, L2-normalized."""
        from PIL import Image

        imgs = [Image.open(p).convert("RGB") for p in paths]
        inputs = self.processor(images=imgs, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            feats = self.model.get_image_features(**inputs)
        return self._norm(feats)

    def embed_text(self, query: str):
        """(EMBED_DIM,) float32, L2-normalized."""
        inputs = self.processor(
            text=[query], padding="max_length", max_length=64,
            return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            feats = self.model.get_text_features(**inputs)
        return self._norm(feats)[0]

    def _norm(self, feats):
        # Newer transformers returns BaseModelOutputWithPooling from
        # get_image_features/get_text_features; the embedding is pooler_output.
        # Older versions return the tensor directly — handle both.
        feats = getattr(feats, "pooler_output", feats)
        arr = feats.float().cpu().numpy()
        norms = self.np.linalg.norm(arr, axis=-1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def index_all(
    manifest: Manifest,
    embedder: "Embedder | None" = None,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH,
    progress: Callable[[str], None] = print,
) -> dict[str, int]:
    """Embed every not-yet-indexed post's frames/slides into the manifest.

    Resumable per POST: the visual_index marker row is written only after all
    of a post's vectors are committed, so an interrupted run redoes at most one
    post. Frames are extracted to a temp dir and deleted after embedding —
    only vectors are kept.
    """
    import shutil
    import tempfile

    np = _require_numpy()
    if embedder is None:
        embedder = Embedder()
    pending = manifest.pending_visual_index(limit=limit)

    # Group file rows by post.
    by_post: dict[str, list[sqlite3.Row]] = {}
    order: list[str] = []
    for r in pending:
        if r["video_id"] not in by_post:
            order.append(r["video_id"])
        by_post.setdefault(r["video_id"], []).append(r)

    tally = {"posts": 0, "vectors": 0, "errors": 0, "missing_file": 0}
    progress(f"index: {len(order)} post(s) pending — model {embedder.model_id}")

    for i, vid in enumerate(order, 1):
        rows = by_post[vid]
        tmp: Path | None = None
        try:
            items: list[tuple[str, float, Path]] = []   # (kind, ts, path)
            if rows[0]["media_kind"] == "video":
                src = Path(rows[0]["local_path"])
                if not src.is_file():
                    tally["missing_file"] += 1
                    continue
                tmp = Path(tempfile.mkdtemp(prefix="tt_frames_"))
                for ts, fpath in fr.extract_frames(src, rows[0]["duration"], workdir=tmp):
                    items.append(("frame", ts, fpath))
            else:
                for r in rows:
                    p = Path(r["local_path"])
                    if p.is_file():
                        items.append(("slide", float(r["num"]), p))
                if not items:
                    tally["missing_file"] += 1
                    continue

            n_stored = 0
            for start in range(0, len(items), batch_size):
                chunk = items[start:start + batch_size]
                vecs = embedder.embed_images([p for _, _, p in chunk])
                for (kind, ts, _), vec in zip(chunk, vecs):
                    manifest.store_frame_vector(
                        vid, kind, ts, vec.astype(np.float16).tobytes())
                    n_stored += 1
            manifest.mark_visual_indexed(vid, n_stored, embedder.model_id)
            manifest.commit()
            tally["posts"] += 1
            tally["vectors"] += n_stored
        except Exception as e:
            tally["errors"] += 1
            progress(f"  [{i}/{len(order)}] {vid}: FAILED ({type(e).__name__}: "
                     f"{str(e)[:150]}) — left pending")
            continue
        finally:
            # The failure path is the ANTICIPATED path (ffmpeg errors, corrupt
            # images) — the tempdir must not outlive the post either way.
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
        if i % 25 == 0 or i == len(order):
            progress(f"  [{i}/{len(order)}] posts={tally['posts']} "
                     f"vectors={tally['vectors']} errors={tally['errors']}")
    return tally


# A transcript FTS hit adds this to the post's frame score. SigLIP cosines on
# this corpus sit around 0.10-0.25, so a spoken-word match reliably lifts a
# post into the visible top-k while frame similarity still orders the rest.
FTS_BLEND_BONUS = 0.2


def search(
    manifest: Manifest,
    query: str,
    k: int = 10,
    embedder: "Embedder | None" = None,
    query_vec=None,
) -> list[dict]:
    """Top-k posts by MAX frame/slide cosine vs the query text, blended with
    transcript full-text hits (a post whose transcript matches the query gets
    FTS_BLEND_BONUS on top of its frame score).

    ``query_vec`` (a pre-normalized (EMBED_DIM,) array) bypasses the model —
    the scoring path is then pure numpy, which is what the tests exercise.
    """
    np = _require_numpy()
    rows = manifest.all_frame_vectors()
    if not rows:
        return []
    if query_vec is None:
        if embedder is None:
            embedder = Embedder()
        query_vec = embedder.embed_text(query)

    mat = np.frombuffer(
        b"".join(r["vector"] for r in rows), dtype=np.float16
    ).reshape(len(rows), -1).astype(np.float32)
    scores = mat @ np.asarray(query_vec, dtype=np.float32)

    best: dict[str, tuple[float, str, float]] = {}   # vid -> (score, kind, ts)
    for r, s in zip(rows, scores):
        cur = best.get(r["video_id"])
        if cur is None or s > cur[0]:
            best[r["video_id"]] = (float(s), r["kind"], r["ts"])

    fts = manifest.transcript_fts_matches(query)
    for vid in fts:
        score, kind, ts = best.get(vid, (0.0, "transcript", 0.0))
        best[vid] = (score + FTS_BLEND_BONUS, kind, ts)

    top = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:k]
    out = []
    for vid, (score, kind, ts) in top:
        post = manifest.conn.execute(
            """
            SELECT p.author_nickname, p.author_unique_id, p.caption,
                   p.canonical_url, t.text AS transcript
            FROM posts p LEFT JOIN transcripts t ON t.video_id = p.video_id
            WHERE p.video_id = ?
            """, (vid,)).fetchone()
        media = manifest.conn.execute(
            "SELECT local_path FROM media_files WHERE video_id=? ORDER BY num LIMIT 1",
            (vid,)).fetchone()
        out.append({
            "video_id": vid,
            "score": score,
            "match_kind": kind,
            "match_ts": ts,
            "transcript_hit": vid in fts,
            "author": (post["author_nickname"] or post["author_unique_id"]) if post else None,
            "caption": post["caption"] if post else None,
            "canonical_url": post["canonical_url"] if post else None,
            "transcript_snippet": (post["transcript"] or "")[:160] if post else "",
            "local_path": media["local_path"] if media else None,
        })
    return out
