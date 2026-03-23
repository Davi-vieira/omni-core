"""PyInstaller build helper for the Omni-Core Windows executable."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from PyInstaller.__main__ import run as pyinstaller_run
except ImportError as exc:  # pragma: no cover - build-time dependency
    raise SystemExit(
        "PyInstaller nao esta instalado. Execute: pip install pyinstaller"
    ) from exc

from tools.obfuscate_project import ObfuscationError, create_obfuscated_workspace

APP_NAME = "Omni-Core-ERP"
PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
OBFUSCATED_WORKSPACE = BUILD_DIR / "obfuscated_workspace"
ASSET_SUFFIXES = (".qss", ".ico")
HIDDEN_IMPORTS = (
    "matplotlib",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.qt_compat",
    "sqlite3",
    "_sqlite3",
    "PyQt6",
    "PyQt6.sip",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "flask",
    "werkzeug",
    "werkzeug.serving",
    "click",
    "itsdangerous",
    "jinja2",
    "blinker",
    "src.core.engine",
    "src.core.fsm",
    "src.core.audit_logger",
    "src.database.backup_manager",
    "src.database.connection",
    "src.network.whatsapp_gateway",
    "src.database.repositories",
    "src.ia.brain",
    "src.security.license_manager",
    "src.ui.dashboard_module",
    "src.ui.main_window",
    "src.ui.product_manager",
    "src.ui.sales_module",
    "src.ui.settings_module",
    "src.ui.style_manager",
)


def _data_separator() -> str:
    return ";" if os.name == "nt" else ":"


def _add_data_argument(source_path: Path, destination: str) -> str:
    return f"--add-data={source_path}{_data_separator()}{destination}"


def _discover_icon() -> Path | None:
    candidates = sorted(PROJECT_ROOT.rglob("*.ico"))
    return candidates[0] if candidates else None


def _discover_assets() -> list[tuple[Path, str]]:
    assets: list[tuple[Path, str]] = []

    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        assets.append((data_dir, "data"))

    theme_file = PROJECT_ROOT / "theme.json"
    if theme_file.exists():
        assets.append((theme_file, "."))

    seen_files: set[Path] = set()
    for candidate in sorted(PROJECT_ROOT.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in ASSET_SUFFIXES:
            continue
        if candidate in seen_files:
            continue
        seen_files.add(candidate)
        relative_parent = candidate.relative_to(PROJECT_ROOT).parent.as_posix()
        assets.append((candidate, relative_parent or "."))

    return assets


def build_executable(*, console_debug: bool = False, app_name: str = APP_NAME) -> None:
    """Run PyInstaller with the Omni-Core desktop packaging defaults."""

    workspace = create_obfuscated_workspace(output_root=OBFUSCATED_WORKSPACE)
    entrypoint = workspace.root / "app.py"

    pyinstaller_args = [
        str(entrypoint),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        app_name,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "work"),
        "--specpath",
        str(BUILD_DIR / "spec"),
        "--paths",
        str(workspace.root),
    ]

    if console_debug:
        pyinstaller_args.append("--console")
    else:
        pyinstaller_args.append("--windowed")

    if console_debug:
        pyinstaller_args.append("--debug=imports")

    for hidden_import in HIDDEN_IMPORTS:
        pyinstaller_args.extend(["--hidden-import", hidden_import])

    for source_path, destination in _discover_assets():
        pyinstaller_args.append(_add_data_argument(source_path, destination))

    icon_path = _discover_icon()
    if icon_path is not None:
        pyinstaller_args.extend(["--icon", str(icon_path)])
        print(f"[build] Icone selecionado: {icon_path}")
    else:
        print("[build] Nenhum arquivo .ico encontrado. O build seguira sem icone personalizado.")

    print(f"[build] Entrada ofuscada: {entrypoint}")
    print(f"[build] Workspace ofuscado: {workspace.root}")
    print(f"[build] Modo console debug: {console_debug}")
    print("[build] Assets incluidos:")
    for source_path, destination in _discover_assets():
        print(f"  - {source_path} -> {destination}")

    pyinstaller_run(pyinstaller_args)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera o executavel Windows do Omni-Core com PyInstaller."
    )
    parser.add_argument(
        "--console-debug",
        action="store_true",
        help="Gera uma variante com console para ver logs de debug e bootstrap.",
    )
    parser.add_argument(
        "--name",
        default=APP_NAME,
        help="Nome do executavel final.",
    )
    args = parser.parse_args()

    try:
        build_executable(console_debug=args.console_debug, app_name=args.name)
    except ObfuscationError as exc:
        raise SystemExit(
            "Build interrompido: a etapa de ofuscacao falhou e nenhum executavel desprotegido foi gerado.\n"
            f"Detalhes: {exc}"
        ) from exc


if __name__ == "__main__":
    main()
