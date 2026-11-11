from pathlib import Path

import pytest

from skillware.ingest.paths import contained_path, private_cache


def test_containment_rejects_sibling_and_traversal(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    for candidate in (tmp_path / "outside.md", root / ".." / "outside.md"):
        with pytest.raises(ValueError, match="outside declared root"):
            contained_path(candidate, root)


def test_containment_rejects_symlink_root_and_nested_escape(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        contained_path(root / "escape" / "talk.md", root)
    with pytest.raises(ValueError, match="symlink"):
        contained_path(root / "escape" / "talk.md", root / "escape")
    with pytest.raises(ValueError, match="symlink"):
        contained_path(root / "escape" / ".." / "talk.md", root)


def test_private_cache_permissions_and_release_pack_refusal(tmp_path):
    cache = private_cache(tmp_path / "private" / "cache", tmp_path)
    assert cache.stat().st_mode & 0o777 == 0o700
    assert cache.parent.stat().st_mode & 0o777 == 0o700
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("Synthetic pack marker.")
    with pytest.raises(ValueError, match="release packs"):
        private_cache(pack / "cache", tmp_path)


def test_private_cache_rejects_this_repository_without_writes():
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="outside the repository"):
        private_cache(repo / "rejected-private-cache", repo)
    assert not (repo / "rejected-private-cache").exists()
