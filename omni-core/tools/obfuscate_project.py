"""Generate an obfuscated build workspace for Omni-Core ERP."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "build" / "obfuscated_workspace"
DEFAULT_TARGETS = (
    "src/core",
    "src/database",
    "src/ia",
    "src/security",
)
ROOT_FILES = ("app.py", "__init__.py")
ROOT_DIRECTORIES = ("data",)
_WINDOWS_NO_WINDOW = 0x08000000


class ObfuscationError(RuntimeError):
    """Raised when the PyArmor stage cannot produce a secure workspace."""


@dataclass(frozen=True, slots=True)
class ObfuscationWorkspace:
    """Description of the generated obfuscated workspace."""

    root: Path
    targets: tuple[str, ...]
    mix_str: bool


def create_obfuscated_workspace(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    mix_str: bool = True,
) -> ObfuscationWorkspace:
    """Create a clean build workspace with selected packages obfuscated."""

    ensure_pyarmor_available()

    resolved_output = output_root.resolve()
    if resolved_output.exists():
        shutil.rmtree(resolved_output)
    resolved_output.mkdir(parents=True, exist_ok=True)

    _copy_project_skeleton(resolved_output, targets)

    for target in targets:
        _run_pyarmor_for_target(
            target=PROJECT_ROOT / target,
            output_root=resolved_output / "src",
            mix_str=mix_str,
        )

    return ObfuscationWorkspace(
        root=resolved_output,
        targets=targets,
        mix_str=mix_str,
    )


def ensure_pyarmor_available() -> None:
    """Ensure the local environment can execute ``pyarmor``."""

    try:
        completed = _run_command(("pyarmor", "--version"), check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ObfuscationError(
            "PyArmor nao esta disponivel. Execute: pip install pyarmor"
        ) from exc

    if completed.returncode != 0:
        raise ObfuscationError("PyArmor foi encontrado, mas nao respondeu corretamente.")


def _copy_project_skeleton(output_root: Path, targets: tuple[str, ...]) -> None:
    for file_name in ROOT_FILES:
        source_file = PROJECT_ROOT / file_name
        if source_file.exists():
            shutil.copy2(source_file, output_root / file_name)

    for directory_name in ROOT_DIRECTORIES:
        source_directory = PROJECT_ROOT / directory_name
        if source_directory.exists():
            shutil.copytree(source_directory, output_root / directory_name)

    target_roots = {Path(target).parts[1] for target in targets if len(Path(target).parts) > 1}

    def ignore_src(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = {
            name for name in names
            if name == "__pycache__" or name.endswith(".pyc")
        }
        current_path = Path(directory)
        try:
            relative_path = current_path.resolve().relative_to((PROJECT_ROOT / "src").resolve())
        except ValueError:
            return ignored

        if relative_path == Path("."):
            ignored.update(target_roots)
        return ignored

    shutil.copytree(
        PROJECT_ROOT / "src",
        output_root / "src",
        ignore=ignore_src,
    )


def _run_pyarmor_for_target(
    *,
    target: Path,
    output_root: Path,
    mix_str: bool,
) -> None:
    command = [
        "pyarmor",
        "gen",
        "-O",
        str(output_root),
        "-r",
        "-i",
        "--obf-code",
        "1",
    ]
    if mix_str:
        command.append("--mix-str")
    command.append(str(target))

    completed = _run_command(tuple(command), check=False)
    if completed.returncode == 0:
        print(completed.stdout.rstrip())
        return

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    combined_output = "\n".join(part for part in (stdout, stderr) if part)

    if mix_str and "out of license" in combined_output.lower():
        raise ObfuscationError(
            "PyArmor nao conseguiu aplicar a protecao de strings (--mix-str). "
            "A licenca atual nao suporta este nivel de ofuscacao; o build foi abortado."
        )

    raise ObfuscationError(
        "Falha na ofuscacao do pacote "
        f"{target}. Saida do PyArmor:\n{combined_output or 'sem detalhes'}"
    )


def _run_command(command: tuple[str, ...], *, check: bool) -> subprocess.CompletedProcess[str]:
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=check,
        creationflags=_WINDOWS_NO_WINDOW,
        startupinfo=startupinfo,
    )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera um workspace temporario com pacotes protegidos por PyArmor."
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Diretorio de saida do workspace ofuscado.",
    )
    parser.add_argument(
        "--no-mix-str",
        action="store_true",
        help="Desabilita a protecao de strings. Use apenas para validacao local.",
    )
    args = parser.parse_args()

    workspace = create_obfuscated_workspace(
        output_root=Path(args.output),
        mix_str=not args.no_mix_str,
    )

    print(f"Workspace ofuscado gerado em: {workspace.root}")
    print(f"Pacotes protegidos: {', '.join(workspace.targets)}")
    print(f"Protecao de strings ativa: {workspace.mix_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
