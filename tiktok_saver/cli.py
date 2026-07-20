"""tiktok-saver CLI.

    tiktok-saver login                     # one-time interactive login into the tool profile
    tiktok-saver enumerate --surface all   # read your lists into the manifest (no downloads)
    tiktok-saver download  --surface all   # fetch bytes for pending posts
    tiktok-saver run       --surface all   # enumerate + download in one pass
    tiktok-saver reconcile user_data.json  # optional: diff official export vs manifest
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

    sp = sub.add_parser("reconcile", help="diff official data export vs manifest")
    add_common(sp)
    sp.add_argument("export_path", help="path to user_data.json from TikTok's data export")
    sp.set_defaults(func=_cmd_reconcile)

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
