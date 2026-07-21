"""Keyframe extraction for the visual index.

One ffmpeg pass per video at an adaptive sample rate: short videos get a frame
every SAMPLE_INTERVAL_S seconds, long ones spread MAX_FRAMES_PER_VIDEO evenly —
so a 30-second cut and a 30-minute compilation both land at a bounded,
deterministic frame set with computable timestamps (frame n at n/fps seconds).
Deliberately NOT scene-detection: deterministic timestamps need no stderr
parsing, and for retrieval a bounded uniform sample characterizes a TikTok as
well as shot boundaries do (the standard video-retrieval setup is 8 uniform
frames per clip).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

SAMPLE_INTERVAL_S = 2.0          # one frame per 2s of video…
MAX_FRAMES_PER_VIDEO = 40        # …but never more than this many
FRAME_HEIGHT = 384               # SigLIP 2 input side; no point extracting larger


def sample_fps(duration_s: float | None) -> float:
    """Frames/second to sample a video of this duration at (>= 1 frame)."""
    if not duration_s or duration_s <= 0:
        return 1.0 / SAMPLE_INTERVAL_S
    return min(1.0 / SAMPLE_INTERVAL_S, MAX_FRAMES_PER_VIDEO / duration_s)


def ffmpeg_cmd(video_path: Path, out_pattern: Path, fps: float) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps={fps:.6f},scale=-2:{FRAME_HEIGHT}",
        "-frames:v", str(MAX_FRAMES_PER_VIDEO),
        "-q:v", "3",
        str(out_pattern),
    ]


def extract_frames(
    video_path: Path,
    duration_s: float | None,
    workdir: Path | None = None,
    run=subprocess.run,
) -> list[tuple[float, Path]]:
    """Extract sampled frames to JPEGs; return [(timestamp_seconds, path)].

    Frames land in a caller-owned (or temporary) directory; callers embed them
    and discard — frames are derivable, only vectors are kept.
    """
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="tt_frames_"))
    workdir.mkdir(parents=True, exist_ok=True)
    fps = sample_fps(duration_s)
    pattern = workdir / "f_%04d.jpg"
    result = run(ffmpeg_cmd(video_path, pattern, fps),
                 capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        # A handful of TikToks carry color-space metadata the scale filter
        # rejects ("Invalid color space"). Retry once without scaling — the
        # embedding processor resizes anyway; native-res JPEGs just cost a
        # little more temp disk.
        cmd = [a for a in ffmpeg_cmd(video_path, pattern, fps)]
        cmd[cmd.index("-vf") + 1] = f"fps={fps:.6f}"
        result = run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed on {video_path.name}: {(result.stderr or '')[:300]}")
    out: list[tuple[float, Path]] = []
    for f in sorted(workdir.glob("f_*.jpg")):
        n = int(f.stem.split("_")[1])          # ffmpeg numbers from 1
        out.append(((n - 1) / fps, f))
    return out
