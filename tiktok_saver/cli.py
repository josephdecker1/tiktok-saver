"""tiktok-saver CLI.

    tiktok-saver login                     # one-time interactive login into the tool profile
    tiktok-saver enumerate --surface all   # read your lists into the manifest (no downloads)
    tiktok-saver download  --surface all   # fetch bytes for pending posts
    tiktok-saver run       --surface all   # enumerate + download in one pass
    tiktok-saver reconcile user_data.json  # optional: diff official export vs manifest
    tiktok-saver transcribe                # send downloaded videos to the GPU Whisper box
    tiktok-saver export-transcripts        # per-post transcript markdown for text indexing
    tiktok-saver index-frames              # embed video frames + slideshow images (SigLIP 2)
    tiktok-saver search "query"            # visual+transcript search over the archive
    tiktok-saver status                    # counts by state; what's pending/gone/private
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, mapping, session
from .manifest import Manifest

DEFAULT_OUT = Path.home() / "Downloads" / "TikTok-collections"


def _manifest_path(username: str, out_dir: Path) -> Path:
    return out_dir / f"tt_manifest_{username}.db"


def _cmd_login(args) -> int:
    print(f"Launching Chrome with the tool profile ({session.DEFAULT_PROFILE_DIR}).")
    print("Log into TikTok in the window, then press Enter here.")
    with session.browser_context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(session.TIKTOK_HOME, wait_until="domcontentloaded")
        try:
            input("  [waiting] press Enter once you're logged in… ")
        except EOFError:
            print("  (no TTY — give the browser time, then re-run in a terminal)")
        if session.is_logged_in(ctx):
            print("✓ sessionid cookie present — you're logged in. Profile saved.")
            return 0
        print("✗ no sessionid cookie found. Did the login complete?")
        return 1


def _cmd_enumerate(args) -> int:
    from . import enumerate as enum  # lazy: needs Playwright

    out_dir = Path(args.out)
    manifest = Manifest(_manifest_path(args.username, out_dir))
    surfaces = _resolve_or_die(args.surface)
    with session.browser_context(headless=args.headless) as ctx:
        if not session.is_logged_in(ctx):
            print("✗ not logged in. Run `tiktok-saver login` first.", file=sys.stderr)
            return 1
        session.export_cookies_txt(ctx, out_dir / f"cookies_{args.username}.txt")
        for surface in surfaces:
            if surface.key == "collection":
                res = enum.enumerate_collections(ctx, args.username, manifest)
                for name, n in res.items():
                    print(f"  collection '{name}': {n} item(s)")
            else:
                n = enum.enumerate_item_surface(ctx, args.username, surface, manifest)
                print(f"  {surface.ui_name}: {n} item(s)")
    _print_status(manifest)
    manifest.close()
    return 0


def _cmd_download(args) -> int:
    from . import download as dl

    out_dir = Path(args.out)
    manifest = Manifest(_manifest_path(args.username, out_dir))
    cookies = out_dir / f"cookies_{args.username}.txt"
    if not cookies.exists():
        print(f"✗ no cookies file at {cookies}. Run `enumerate` first.", file=sys.stderr)
        return 1
    stypes = mapping.keys_for(args.surface)      # None for 'all'
    tally = dl.download_all(
        manifest, out_dir, cookies,
        source_types=stypes, photos_only=args.photos_only, videos_only=args.videos_only,
        limit=args.limit,
    )
    print("download tally:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to do")
    manifest.close()
    return 0


def _cmd_run(args) -> int:
    rc = _cmd_enumerate(args)
    if rc != 0:
        return rc
    return _cmd_download(args)


def _cmd_sync(args) -> int:
    """Incremental sync: capture only posts saved since last run, then download
    them. `--full` re-scrapes every surface end-to-end instead of stopping at the
    known watermark. (Detecting un-saves / removals is a later phase — `--full`
    does not yet prune them.)"""
    from . import enumerate as enum

    out_dir = Path(args.out)
    manifest = Manifest(_manifest_path(args.username, out_dir))
    surfaces = _resolve_or_die(args.surface)
    incremental = not args.full
    mode = "full re-scrape" if args.full else "incremental"
    print(f"sync ({mode}) — surfaces: {', '.join(s.key for s in surfaces)}")

    # Snapshot known ids per surface so we can report what's NEW this run.
    before = {s.key: manifest.known_video_ids(s.key) for s in surfaces}

    with session.browser_context(headless=args.headless) as ctx:
        if not session.is_logged_in(ctx):
            # A scheduled run with an expired session must be loud, not a silent
            # no-op (plan: session-expiry handling).
            print("✗ not logged in (sessionid missing). Run `tiktok-saver login`.",
                  file=sys.stderr)
            manifest.close()
            return 2
        session.export_cookies_txt(ctx, out_dir / f"cookies_{args.username}.txt")
        for surface in surfaces:
            if surface.key == "collection":
                enum.enumerate_collections(
                    ctx, args.username, manifest,
                    incremental=incremental, stop_after_known=args.stop_after_known)
            else:
                enum.enumerate_item_surface(
                    ctx, args.username, surface, manifest,
                    incremental=incremental, stop_after_known=args.stop_after_known)

    all_new: set[str] = set()
    for s in surfaces:
        new = manifest.known_video_ids(s.key) - before[s.key]
        all_new |= new           # union: a post new to two surfaces counts once
        print(f"  {s.ui_name}: {len(new)} new")
    print(f"sync: {len(all_new)} new post(s) captured")
    _print_status(manifest)

    if not args.no_download:
        from . import download as dl
        cookies = out_dir / f"cookies_{args.username}.txt"
        stypes = mapping.keys_for(args.surface)
        tally = dl.download_all(
            manifest, out_dir, cookies, source_types=stypes,
            photos_only=args.photos_only, videos_only=args.videos_only)
        print("download tally:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items()))
              or "nothing to do")
    manifest.close()
    return 0


def _cmd_reconcile(args) -> int:
    from . import reconcile as rec

    out_dir = Path(args.out)
    manifest = Manifest(_manifest_path(args.username, out_dir))
    result = rec.reconcile(manifest, args.export_path)
    for label, missing in result.items():
        print(f"{label}: {len(missing)}")
        for link in missing[:20]:
            print(f"    {link}")
    manifest.close()
    return 0


def _cmd_transcribe(args) -> int:
    """Send downloaded videos without a transcript to the local GPU Whisper box
    and store the text in the manifest. Serial (the box takes one job at a
    time), resumable (re-runs pick up exactly the posts still missing)."""
    from . import transcribe as tr

    api_key = tr.resolve_api_key(args.api_key_env)
    if not api_key:
        print(f"✗ no API key in ${args.api_key_env}. Export it first "
              f"(GCP-SM condensr_backend_home_transcription-api-key).", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    manifest = Manifest(_manifest_path(args.username, out_dir))
    try:
        tally = tr.transcribe_all(
            manifest, endpoint=args.endpoint, api_key=api_key, limit=args.limit)
    except tr.BoxDown as e:
        print(f"✗ {e}", file=sys.stderr)
        manifest.close()
        return 3
    except (ValueError, RuntimeError) as e:
        print(f"✗ {e}", file=sys.stderr)
        manifest.close()
        return 2
    print("transcribe tally:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    counts = manifest.transcript_counts()
    print(f"transcripts total: {counts['transcribed']} ({counts['empty']} empty)")
    manifest.close()
    return 0 if tally["errors"] == 0 else 1


def _cmd_export_transcripts(args) -> int:
    """Write one markdown file per transcribed post into <out>/transcripts/ so
    a text indexer (QMD) can pick the spoken content up. Deterministic and
    idempotent — every run regenerates from the manifest."""
    manifest = Manifest(_manifest_path(args.username, Path(args.out)))
    dest = Path(args.out) / "transcripts"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in manifest.transcript_export_rows():
        author = r["author_nickname"] or r["author_unique_id"] or "?"
        lines = [
            f"# {author} — TikTok {r['video_id']}",
            "",
            f"- author: {author} (@{r['author_unique_id']})",
            f"- url: {r['canonical_url']}",
            f"- saved in: {r['collections'] or 'favorites'}",
            f"- language: {r['language'] or '?'}  ·  duration: {r['duration'] or '?'}s",
            "",
        ]
        if r["caption"]:
            lines += [f"**Caption:** {r['caption']}", ""]
        lines += ["## Transcript", "", r["text"], ""]
        (dest / f"{r['video_id']}.md").write_text("\n".join(lines), encoding="utf-8")
        n += 1
    print(f"exported {n} transcript file(s) to {dest}")
    manifest.close()
    return 0


def _cmd_index_frames(args) -> int:
    """Embed sampled video frames + slideshow images into the manifest's visual
    index (SigLIP 2 on MPS). Resumable per post; needs the [embed] extra."""
    from . import embed

    manifest = Manifest(_manifest_path(args.username, Path(args.out)))
    try:
        tally = embed.index_all(
            manifest, limit=args.limit, batch_size=args.batch_size)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr)
        manifest.close()
        return 2
    print("index tally:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    counts = manifest.visual_index_counts()
    print(f"visual index total: {counts['posts']} posts, {counts['vectors']} vectors")
    manifest.close()
    return 0 if tally["errors"] == 0 else 1


def _cmd_search(args) -> int:
    """Text search over the visual index; max frame score per post, transcript
    snippet alongside."""
    from . import embed

    manifest = Manifest(_manifest_path(args.username, Path(args.out)))
    try:
        hits = embed.search(manifest, args.query, k=args.k)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr)
        manifest.close()
        return 2
    if not hits:
        print("no results (is the visual index built? run index-frames)")
        manifest.close()
        return 1
    for h in hits:
        loc = f"@{h['match_ts']:.0f}s" if h["match_kind"] == "frame" else f"slide {int(h['match_ts'])}"
        print(f"{h['score']:.3f}  {h['author'] or '?'} ({loc})  {h['canonical_url']}")
        if h["caption"]:
            print(f"       caption: {h['caption'][:120]}")
        if h["transcript_snippet"]:
            print(f"       said: {h['transcript_snippet'][:120]}")
        if h["local_path"]:
            print(f"       file: {h['local_path']}")
    manifest.close()
    return 0


def _cmd_status(args) -> int:
    out_dir = Path(args.out)
    path = _manifest_path(args.username, out_dir)
    if not path.exists():
        print(f"no manifest yet at {path}")
        return 0
    manifest = Manifest(path)
    _print_status(manifest)
    manifest.close()
    return 0


def _print_status(manifest: Manifest) -> None:
    surf = manifest.surface_counts()
    stat = manifest.status_counts()
    cols = manifest.collection_names()
    print("\n— manifest —")
    print("  by surface:", ", ".join(f"{k}={v}" for k, v in sorted(surf.items())) or "(empty)")
    print("  by state:  ", ", ".join(f"{k}={v}" for k, v in sorted(stat.items())) or "(empty)")
    if cols:
        print(f"  collections ({len(cols)}):")
        for _cid, name, n in cols:
            print(f"    {name}: {n}")


def _resolve_or_die(surface_arg: str):
    try:
        return mapping.resolve(surface_arg)
    except KeyError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tiktok-saver", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"tiktok-saver {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, username=True):
        if username:
            sp.add_argument("username", help="your TikTok username, without the @")
        sp.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"output dir + manifest location (default: {DEFAULT_OUT})")

    sp = sub.add_parser("login", help="one-time interactive login into the tool profile")
    sp.set_defaults(func=_cmd_login)

    for name, func in (("enumerate", _cmd_enumerate), ("download", _cmd_download), ("run", _cmd_run)):
        sp = sub.add_parser(name, help=func.__doc__)
        add_common(sp)
        sp.add_argument("--surface", nargs="+", default=["all"],
                        choices=["all", *mapping.ITEM_SURFACE_KEYS],
                        metavar="SURFACE",
                        help="one or more of: all, collections, favorites, liked "
                             "(default: all). e.g. --surface collections favorites")
        sp.add_argument("--headless", action="store_true",
                        help="run Chrome headless (higher anti-bot risk; default off)")
        sp.add_argument("--photos-only", action="store_true", help="download only photo slideshows")
        sp.add_argument("--videos-only", action="store_true", help="download only videos")
        sp.add_argument("--limit", type=int, default=None,
                        help="cap how many posts to download this run (for test batches)")
        sp.set_defaults(func=func)

    sp = sub.add_parser("sync", help="incremental sync: capture + download only new saves")
    add_common(sp)
    sp.add_argument("--surface", nargs="+", default=["collections", "favorites"],
                    choices=["all", *mapping.ITEM_SURFACE_KEYS], metavar="SURFACE",
                    help="surfaces to sync (default: collections favorites)")
    sp.add_argument("--full", action="store_true",
                    help="re-scrape every surface end-to-end instead of stopping at "
                         "the known watermark (does not yet prune removed items)")
    sp.add_argument("--no-download", action="store_true",
                    help="capture new saves into the manifest but don't download")
    sp.add_argument("--stop-after-known", type=int, default=3, metavar="N",
                    help="stop a surface after N consecutive already-known posts (default: 3)")
    sp.add_argument("--headless", action="store_true", help="run Chrome headless")
    sp.add_argument("--photos-only", action="store_true", help="download only photo slideshows")
    sp.add_argument("--videos-only", action="store_true", help="download only videos")
    sp.set_defaults(func=_cmd_sync)

    sp = sub.add_parser("reconcile", help="diff official data export vs manifest")
    add_common(sp)
    sp.add_argument("export_path", help="path to user_data.json from TikTok's data export")
    sp.set_defaults(func=_cmd_reconcile)

    sp = sub.add_parser("transcribe",
                        help="transcribe downloaded videos on the local GPU Whisper box")
    add_common(sp)
    sp.add_argument("--endpoint", default="http://10.0.0.50:8002",
                    help="GPU box base URL (default: %(default)s)")
    sp.add_argument("--api-key-env", default="TRANSCRIPTION_API_KEY", metavar="VAR",
                    help="env var holding the X-API-Key value (default: %(default)s)")
    sp.add_argument("--limit", type=int, default=None,
                    help="cap how many videos to transcribe this run (for test batches)")
    sp.set_defaults(func=_cmd_transcribe)

    sp = sub.add_parser("export-transcripts",
                        help="write per-post transcript markdown for text indexing (QMD)")
    add_common(sp)
    sp.set_defaults(func=_cmd_export_transcripts)

    sp = sub.add_parser("index-frames",
                        help="embed video frames + slideshow images (SigLIP 2, needs [embed] extra)")
    add_common(sp)
    sp.add_argument("--limit", type=int, default=None,
                    help="cap how many posts to index this run (for test batches)")
    sp.add_argument("--batch-size", type=int, default=16,
                    help="images per embedding batch (default: %(default)s)")
    sp.set_defaults(func=_cmd_index_frames)

    sp = sub.add_parser("search", help="text search over the visual index")
    add_common(sp, username=False)
    sp.add_argument("query", help="what to look for, in plain words")
    sp.add_argument("username", nargs="?", default="_jdeck_",
                    help="TikTok username (default: _jdeck_)")
    sp.add_argument("-k", type=int, default=10, help="results to show (default: %(default)s)")
    sp.set_defaults(func=_cmd_search)

    sp = sub.add_parser("status", help="show manifest counts by surface and state")
    add_common(sp)
    sp.set_defaults(func=_cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
