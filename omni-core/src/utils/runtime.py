"""Runtime helpers for source and frozen Omni-Core executions."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def is_frozen_application() -> bool:
    """Return ``True`` when the app is running from a frozen executable."""

    return bool(getattr(sys, "frozen", False))


@lru_cache(maxsize=1)
def get_source_root() -> Path:
    """Return the source checkout root."""

    return SOURCE_ROOT


@lru_cache(maxsize=1)
def get_bundle_root() -> Path:
    """Return the current bundle extraction root.

    In source runs this is the project root. In PyInstaller ``--onefile`` runs,
    this resolves to ``sys._MEIPASS``.
    """

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    return SOURCE_ROOT


@lru_cache(maxsize=1)
def get_runtime_root() -> Path:
    """Return the writable runtime root used by the application."""

    if is_frozen_application():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def get_runtime_data_dir() -> Path:
    """Return the writable data directory for the current runtime."""

    return get_runtime_root() / "data"


def get_bundle_data_dir() -> Path:
    """Return the bundled data directory, if available."""

    return get_bundle_root() / "data"


def iter_asset_files(
    suffixes: tuple[str, ...],
    *,
    prefer_runtime: bool = True,
) -> list[Path]:
    """Return asset files matching the given suffixes across runtime roots."""

    ordered_roots = [get_runtime_root(), get_bundle_root()] if prefer_runtime else [
        get_bundle_root(),
        get_runtime_root(),
    ]

    seen: set[Path] = set()
    results: list[Path] = []
    for root in ordered_roots:
        for suffix in suffixes:
            for candidate in sorted(root.rglob(f"*{suffix}")):
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                results.append(resolved)
    return results


def find_first_asset(
    suffixes: tuple[str, ...],
    *,
    prefer_runtime: bool = True,
) -> Path | None:
    """Return the first matching asset file, if one exists."""

    matches = iter_asset_files(suffixes, prefer_runtime=prefer_runtime)
    return matches[0] if matches else None


def load_stylesheets() -> str:
    """Return the concatenated contents of all discovered QSS files."""

    chunks: list[str] = []
    for stylesheet_path in iter_asset_files((".qss",)):
        chunks.append(stylesheet_path.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)
