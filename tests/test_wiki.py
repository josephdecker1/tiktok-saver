"""Wiki compiler: slugs, topic post selection, prompt grounding, incremental
skip, error-leaves-page-missing, and untrusted-data guard presence."""
import pytest

from tiktok_saver import wiki
from tiktok_saver.manifest import Manifest


def _item(vid, unique="alice", caption="a caption"):
    return {
        "id": vid, "desc": caption, "createTime": 1700000000,
        "author": {"uniqueId": unique, "secUid": f"S_{unique}", "nickname": unique},
        "video": {"duration": 30}, "stats": {},
    }


def _seed(tmp_path):
    m = Manifest(tmp_path / "t.db")
    m.upsert_post(_item("111", caption="sourdough starter day 3"))
    m.add_membership("111", "collection", "c1", "bread", 0)
    m.add_membership("111", "favorites", "_self", "favorites", 0)
    m.set_transcript("111", "feed the starter twice daily", "en", 0.9, 30.0, "m")
    m.upsert_post(_item("222", unique="bob", caption="random save"))
    m.add_membership("222", "favorites", "_self", "favorites", 0)
    m.commit()
    return m


def test_slugify():
    assert wiki.slugify("my body my fat") == "my-body-my-fat"
    assert wiki.slugify("3d printing") == "3d-printing"
    assert wiki.slugify("boooks!!") == "boooks"


def test_topic_posts_collection_vs_uncollected(tmp_path):
    m = _seed(tmp_path)
    bread = m.topic_posts("c1")
    assert [r["video_id"] for r in bread] == ["111"]
    assert bread[0]["transcript"] == "feed the starter twice daily"
    uncollected = m.topic_posts(None)
    assert [r["video_id"] for r in uncollected] == ["222"]   # 111 is in a folder


def test_prompt_grounds_data_and_guards(tmp_path):
    m = _seed(tmp_path)
    p = wiki.build_prompt("bread", m.topic_posts("c1"))
    assert "sourdough starter day 3" in p
    assert "feed the starter twice daily" in p
    assert "https://www.tiktok.com/@alice/video/111" in p
    assert "never an\ninstruction" in p or "never invent" in p   # data-is-data guard
    assert p.count("- author:") == 1


def test_compile_writes_frontmatter_and_body(tmp_path):
    m = _seed(tmp_path)
    out = tmp_path / "wiki"

    def fake_claude(prompt, model, claude_bin):
        return "# bread\n\n## Overview\n\nbody"

    tally = wiki.compile_wiki(m, out, run_claude=fake_claude,
                              progress=lambda s: None)
    assert tally["written"] == 2                     # bread + favorites-uncollected
    page = (out / "bread.md").read_text()
    assert page.startswith("---\ntags: [tiktok/collection]\nsource_count: 1\n")
    assert "# bread" in page
    fav = (out / "favorites-uncollected.md").read_text()
    assert "tags: [tiktok/favorites]" in fav


def test_compile_incremental_skip_and_force(tmp_path):
    m = _seed(tmp_path)
    out = tmp_path / "wiki"
    calls = []

    def fake_claude(prompt, model, claude_bin):
        calls.append(1)
        return "# x\n\nbody"

    wiki.compile_wiki(m, out, run_claude=fake_claude, progress=lambda s: None)
    n_first = len(calls)
    tally = wiki.compile_wiki(m, out, run_claude=fake_claude, progress=lambda s: None)
    assert len(calls) == n_first                     # nothing regenerated
    assert tally["skipped_existing"] == 2
    wiki.compile_wiki(m, out, force=True, run_claude=fake_claude,
                      progress=lambda s: None)
    assert len(calls) == 2 * n_first


def test_compile_topic_filter(tmp_path):
    m = _seed(tmp_path)
    out = tmp_path / "wiki"

    def fake_claude(prompt, model, claude_bin):
        return "# bread\n\nbody"

    tally = wiki.compile_wiki(m, out, topics=["BREAD"], run_claude=fake_claude,
                              progress=lambda s: None)
    assert tally["written"] == 1
    assert (out / "bread.md").exists()
    assert not (out / "favorites-uncollected.md").exists()


def test_compile_error_leaves_page_missing_for_rerun(tmp_path):
    m = _seed(tmp_path)
    out = tmp_path / "wiki"

    def boom(prompt, model, claude_bin):
        raise RuntimeError("model unavailable")

    tally = wiki.compile_wiki(m, out, topics=["bread"], run_claude=boom,
                              progress=lambda s: None)
    assert tally["errors"] == 1 and not (out / "bread.md").exists()


def test_run_claude_salvages_preamble(monkeypatch):
    class R:
        returncode = 0
        stderr = ""
        stdout = "Here is the page:\n\n# bread\n\nbody"

    out = wiki._run_claude("p", "m", run=lambda *a, **k: R())
    assert out.startswith("# bread")
