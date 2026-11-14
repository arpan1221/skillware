"""Declared filesystem boundaries for private ingest inputs and artifacts."""

from __future__ import annotations

import os
from pathlib import Path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _reject_symlinks(path: Path) -> None:
    # Check before resolve: resolving first would hide a symlink at the root or
    # an existing output filename. Reject in-root symlinks too for predictable IO.
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"symlink paths are not allowed: {component}")


def contained_path(path: Path, root: Path, *, must_exist: bool = False) -> Path:
    """Return an absolute path only when it stays within the declared root."""
    # Validate original components before abspath collapses '..': otherwise a
    # supplied symlink/../file could silently select a different source file.
    for supplied in (Path(root), Path(path)):
        _reject_symlinks(supplied if supplied.is_absolute() else Path.cwd() / supplied)
    path, root = _absolute(Path(path)), _absolute(Path(root))
    if not path.is_relative_to(root):
        raise ValueError(f"path is outside declared root: {path}")
    _reject_symlinks(root)
    _reject_symlinks(path)
    resolved_root = root.resolve()
    resolved = path.resolve(strict=must_exist)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"path escapes declared root: {path}")
    return resolved


def private_cache(path: Path, root: Path) -> Path:
    """Create a private cache outside repositories and Agent Skills packs."""
    result = contained_path(path, root)
    repository = Path(__file__).resolve().parents[3]
    if (repository / "pyproject.toml").is_file() and result.is_relative_to(repository):
        raise ValueError("private cache must be outside the repository")
    for parent in (result, *result.parents):
        git = parent / ".git"
        if git.is_file() or (git / "HEAD").is_file() or (parent / "SKILL.md").exists():
            raise ValueError("private cache must be outside repositories/release packs")
    missing = []
    cursor = result
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    if not result.is_dir():
        raise ValueError("private cache must be a directory")
    result.chmod(0o700)
    return result
