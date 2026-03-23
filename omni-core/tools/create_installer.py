"""Generate and optionally compile the Inno Setup installer for Omni-Core ERP."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
DATA_DIR = PROJECT_ROOT / "data"
INSTALLER_DIR = PROJECT_ROOT / "installer"
RELEASES_DIR = PROJECT_ROOT / "releases"
THEME_PATH = PROJECT_ROOT / "theme.json"
LICENSE_PATH = PROJECT_ROOT / "LICENSE.txt"
VERSION_FILE = PROJECT_ROOT / "__init__.py"
DEFAULT_INSTALL_DIR = r"{autopf}\OmniCore"
DEFAULT_GROUP_NAME = "OmniCore"
OUTPUT_BASE_FILENAME = "OmniCore_Setup"
ISS_FILENAME = "OmniCoreInstaller.iss"


class InstallerError(Exception):
    """Raised when the installer generation process cannot continue."""


@dataclass(frozen=True, slots=True)
class BrandingConfig:
    """Branding metadata read from the white-label theme file."""

    app_name: str
    support_provider: str


@dataclass(frozen=True, slots=True)
class InstallerContext:
    """Resolved paths and metadata used to render the `.iss` script."""

    app_name: str
    app_version: str
    support_provider: str
    app_executable: Path
    theme_file: Path
    license_file: Path
    data_dir: Path
    installer_script: Path
    releases_dir: Path
    setup_icon: Path | None
    icon_files: tuple[Path, ...]
    app_id: str


APPDATA_COMPANY_DIR = r"{userappdata}\OmniCore"
APPDATA_DATA_DIR = r"{userappdata}\OmniCore\data"


def load_branding() -> BrandingConfig:
    """Read the white-label branding values from ``theme.json``."""

    if not THEME_PATH.exists():
        return BrandingConfig(app_name="Omni-Core ERP", support_provider="Seu Nome/Empresa")

    try:
        payload = json.loads(THEME_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BrandingConfig(app_name="Omni-Core ERP", support_provider="Seu Nome/Empresa")

    branding = payload.get("branding", {}) if isinstance(payload, dict) else {}
    app_name = str(branding.get("app_name") or "Omni-Core ERP").strip() or "Omni-Core ERP"
    support_provider = (
        str(branding.get("support_provider") or "Seu Nome/Empresa").strip()
        or "Seu Nome/Empresa"
    )
    return BrandingConfig(app_name=app_name, support_provider=support_provider)


def load_version() -> str:
    """Read the project version from the package root."""

    if not VERSION_FILE.exists():
        return "1.0.0"

    match = re.search(
        r"__version__\s*=\s*[\"']([^\"']+)[\"']",
        VERSION_FILE.read_text(encoding="utf-8"),
    )
    if match is None:
        return "1.0.0"
    return match.group(1)


def ensure_license_text(branding: BrandingConfig) -> Path:
    """Guarantee that the installer EULA file exists."""

    if LICENSE_PATH.exists():
        return LICENSE_PATH

    content = textwrap.dedent(
        f"""\
        LICENCA DE USO DE SOFTWARE - {branding.app_name.upper()}

        IMPORTANTE: LEIA COM ATENCAO ANTES DE INSTALAR OU UTILIZAR ESTE SOFTWARE.

        1. CONCESSAO DE LICENCA
        {branding.app_name} e licenciado, e nao vendido. Esta licenca concede ao cliente
        o direito de instalar e utilizar uma copia do software no ambiente autorizado.

        2. RESTRICOES
        Nao e permitido copiar, redistribuir, sublicenciar, burlar travas de seguranca,
        nem tentar fazer engenharia reversa do software.

        3. DADOS
        Os dados operacionais pertencem ao cliente. Recomenda-se a manutencao de backups
        periodicos antes de qualquer reinstalacao ou manutencao.

        4. SUPORTE
        O suporte tecnico e prestado por: {branding.support_provider}.

        5. ACEITE
        Ao instalar ou utilizar este software, o usuario declara que leu, compreendeu e
        aceitou os termos desta licenca.
        """
    )
    LICENSE_PATH.write_text(content, encoding="utf-8")
    return LICENSE_PATH


def discover_executable(explicit_path: Path | None = None) -> Path:
    """Return the executable that should be packaged into the installer."""

    if explicit_path is not None:
        resolved = explicit_path.resolve()
        if not resolved.exists():
            raise InstallerError(f"Executavel informado nao encontrado: {resolved}")
        return resolved

    candidates = [
        candidate for candidate in DIST_DIR.glob("*.exe")
        if OUTPUT_BASE_FILENAME.lower() not in candidate.name.lower()
    ]
    if not candidates:
        raise InstallerError("Nenhum executavel encontrado em dist/. Gere o build antes do instalador.")

    def score(path: Path) -> tuple[int, float]:
        name = path.name.lower()
        priority = 0
        if "obf" in name:
            priority += 100
        if "hwid" in name:
            priority += 40
        if "debug" in name:
            priority -= 80
        return priority, path.stat().st_mtime

    return sorted(candidates, key=score, reverse=True)[0]


def discover_icon_files() -> tuple[Path, ...]:
    """Return project icon files excluding transient build folders."""

    blocked_parts = {"build", "__pycache__", "dist", "releases"}
    icons: list[Path] = []
    for candidate in PROJECT_ROOT.rglob("*.ico"):
        if any(part in blocked_parts for part in candidate.relative_to(PROJECT_ROOT).parts):
            continue
        icons.append(candidate.resolve())
    return tuple(sorted(icons))


def resolve_iscc_path() -> Path | None:
    """Try to locate the Inno Setup compiler on Windows."""

    command_path = shutil.which("ISCC.exe")
    if command_path:
        return Path(command_path).resolve()

    common_candidates = (
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    )
    for candidate in common_candidates:
        if candidate.exists():
            return candidate.resolve()

    try:
        import winreg  # type: ignore
    except ImportError:
        return None

    registry_keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"),
    )
    for hive, subkey in registry_keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        candidate = Path(str(install_location)) / "ISCC.exe"
        if candidate.exists():
            return candidate.resolve()

    return None


def build_context(explicit_executable: Path | None = None) -> InstallerContext:
    """Resolve the installer metadata and required source files."""

    branding = load_branding()
    license_file = ensure_license_text(branding)

    if not THEME_PATH.exists():
        raise InstallerError("theme.json nao encontrado na raiz do projeto.")

    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)

    icon_files = discover_icon_files()
    setup_icon = icon_files[0] if icon_files else None

    app_name = branding.app_name
    app_id = "{{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"omni-core::{app_name}")).upper() + "}"
    return InstallerContext(
        app_name=app_name,
        app_version=load_version(),
        support_provider=branding.support_provider,
        app_executable=discover_executable(explicit_executable),
        theme_file=THEME_PATH.resolve(),
        license_file=license_file.resolve(),
        data_dir=DATA_DIR.resolve(),
        installer_script=(INSTALLER_DIR / ISS_FILENAME).resolve(),
        releases_dir=RELEASES_DIR.resolve(),
        setup_icon=setup_icon,
        icon_files=icon_files,
        app_id=app_id,
    )


def to_inno_path(path: Path) -> str:
    """Render a Windows path string suitable for Inno Setup."""

    return str(path.resolve())


def iter_file_entries(context: InstallerContext) -> Iterable[str]:
    """Yield the `[Files]` section lines for the installer."""

    yield (
        f'Source: "{to_inno_path(context.app_executable)}"; DestDir: "{{app}}"; '
        "Flags: ignoreversion"
    )
    yield (
        f'Source: "{to_inno_path(context.theme_file)}"; DestDir: "{{app}}"; '
        "Flags: ignoreversion"
    )

    if context.data_dir.exists():
        data_files = sorted(path for path in context.data_dir.rglob("*") if path.is_file())
        for data_file in data_files:
            if data_file.name.lower() == "omni_core.db":
                continue
            relative = data_file.relative_to(context.data_dir)
            destination = Path("{app}") / "data" / relative.parent
            destination_text = str(destination).replace("/", "\\")
            yield (
                f'Source: "{to_inno_path(data_file)}"; DestDir: "{destination_text}"; '
                "Flags: ignoreversion"
            )

    for icon_file in context.icon_files:
        relative = icon_file.relative_to(PROJECT_ROOT)
        destination = Path("{app}") / relative.parent
        destination_text = str(destination).replace("/", "\\")
        yield (
            f'Source: "{to_inno_path(icon_file)}"; DestDir: "{destination_text}"; '
            "Flags: ignoreversion"
        )


def render_iss(context: InstallerContext) -> str:
    """Render the Inno Setup script using the resolved project artifacts."""

    files_section = "\n".join(iter_file_entries(context))
    setup_icon_line = ""
    if context.setup_icon is not None:
        setup_icon_line = f'SetupIconFile={to_inno_path(context.setup_icon)}\n'

    return textwrap.dedent(
        f"""\
        #define MyAppName "{context.app_name}"
        #define MyAppVersion "{context.app_version}"
        #define MyAppPublisher "{context.support_provider}"
        #define MyAppExeName "{context.app_executable.name}"
        #define MyAppId "{context.app_id}"

        [Setup]
        AppId={{#MyAppId}}
        AppName={{#MyAppName}}
        AppVersion={{#MyAppVersion}}
        AppVerName={{#MyAppName}} {{#MyAppVersion}}
        AppPublisher={{#MyAppPublisher}}
        DefaultDirName={DEFAULT_INSTALL_DIR}
        DefaultGroupName={DEFAULT_GROUP_NAME}
        DisableProgramGroupPage=no
        LicenseFile={to_inno_path(context.license_file)}
        OutputDir={to_inno_path(context.releases_dir)}
        OutputBaseFilename={OUTPUT_BASE_FILENAME}
        Compression=lzma
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=admin
        ArchitecturesAllowed=x64compatible
        ArchitecturesInstallIn64BitMode=x64compatible
        UsePreviousAppDir=yes
        UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
        {setup_icon_line.rstrip()}

        [Languages]
        Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\\BrazilianPortuguese.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

        [Dirs]
        Name: "{APPDATA_COMPANY_DIR}"
        Name: "{APPDATA_DATA_DIR}"; Permissions: users-modify

        [Files]
        {files_section}

        [Icons]
        Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon
        Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
        Name: "{{group}}\\Desinstalar {{#MyAppName}}"; Filename: "{{uninstallexe}}"

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Executar {{#MyAppName}} agora"; Flags: nowait postinstall skipifsilent

        [UninstallDelete]
        Type: filesandordirs; Name: "{APPDATA_DATA_DIR}"; Check: ShouldDeleteAppData
        Type: dirifempty; Name: "{APPDATA_COMPANY_DIR}"; Check: ShouldDeleteAppData

        [Code]
        var
          DeleteAppDataOnUninstall: Boolean;

        procedure InitializeWizard();
        begin
          WizardForm.WelcomeLabel2.Caption :=
            'Este assistente vai instalar o ' + ExpandConstant('{{#MyAppName}}') + ' em seu computador.' + #13#10 +
            'Os dados locais e a licenca continuarao protegidos pelo ecossistema Omni-Core.';
        end;

        function InitializeUninstall(): Boolean;
        begin
          DeleteAppDataOnUninstall := False;
          Result := True;
        end;

        procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
        var
          Answer: Integer;
        begin
          if CurUninstallStep = usUninstall then
          begin
            Answer := MsgBox(
              'Deseja apagar os dados de vendas e configuracoes?' + #13#10 +
              'Escolha "Nao" para manter a pasta em AppData e preservar o historico local.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2
            );
            DeleteAppDataOnUninstall := (Answer = IDYES);
          end;
        end;

        function ShouldDeleteAppData(): Boolean;
        begin
          Result := DeleteAppDataOnUninstall;
        end;
        """
    ).strip() + "\n"


def write_iss_script(context: InstallerContext) -> Path:
    """Create or update the `.iss` script on disk."""

    script_contents = render_iss(context)
    context.installer_script.parent.mkdir(parents=True, exist_ok=True)
    context.installer_script.write_text(script_contents, encoding="utf-8")
    return context.installer_script


def compile_installer(iscc_path: Path, iss_path: Path) -> Path:
    """Invoke Inno Setup and return the generated installer path."""

    command = [str(iscc_path), str(iss_path)]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise InstallerError(
            "Falha ao compilar o instalador com o Inno Setup.\n"
            f"Comando: {' '.join(command)}\n"
            f"Saida:\n{completed.stdout}\n{completed.stderr}"
        )

    installer_path = RELEASES_DIR / f"{OUTPUT_BASE_FILENAME}.exe"
    if not installer_path.exists():
        raise InstallerError(
            f"O Inno Setup terminou sem gerar o instalador esperado em: {installer_path}"
        )

    if completed.stdout.strip():
        print(completed.stdout.strip())
    return installer_path


def print_hwid_instructions() -> None:
    """Print the post-build activation instructions for the client."""

    print()
    print("Ativacao do cliente / primeiro license.key")
    print("1. Instale o Omni-Core normalmente no computador do lojista.")
    print("2. No computador do lojista, colete o HWID com este comando do PowerShell:")
    print(
        '   powershell -NoProfile -Command "$board=(Get-CimInstance Win32_ComputerSystemProduct | '
        'Select-Object -First 1 -ExpandProperty UUID); '
        '$cpu=(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty ProcessorId); '
        'Write-Output ((\'BOARD=\' + $board.Trim().ToUpper()) + \'|CPU=\' + $cpu.Trim().ToUpper())"'
    )
    print("3. Peca ao lojista para lhe enviar exatamente a string BOARD=...|CPU=...")
    print("4. Gere a licenca com:")
    print('   python tools\\generate_license.py "BOARD=...|CPU=..."')
    print("5. Entregue o arquivo license.key ao lado do executavel instalado.")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for generating and optionally compiling the installer."""

    parser = argparse.ArgumentParser(
        description="Gera o script .iss e compila o instalador profissional do Omni-Core."
    )
    parser.add_argument(
        "--exe",
        type=Path,
        help="Executavel especifico a ser empacotado. Por padrao o script escolhe o melhor candidato em dist/.",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Gera apenas o arquivo .iss, sem chamar o ISCC.exe.",
    )
    args = parser.parse_args(argv)

    context = build_context(args.exe)
    iss_path = write_iss_script(context)

    print(f"[installer] Script Inno Setup gerado em: {iss_path}")
    print(f"[installer] Executavel selecionado: {context.app_executable}")
    print(f"[installer] Theme incluido: {context.theme_file}")
    print(f"[installer] Data incluida: {context.data_dir}")
    if context.setup_icon is not None:
        print(f"[installer] Icone do setup: {context.setup_icon}")
    else:
        print("[installer] Nenhum .ico encontrado. O instalador seguira sem icone customizado.")

    if args.generate_only:
        print("[installer] Modo generate-only: compilacao com Inno Setup foi pulada.")
        print_hwid_instructions()
        return 0

    iscc_path = resolve_iscc_path()
    if iscc_path is None:
        raise InstallerError(
            "ISCC.exe nao foi encontrado. Instale o Inno Setup 6 ou rode com --generate-only."
        )

    print(f"[installer] Compilando com: {iscc_path}")
    installer_path = compile_installer(iscc_path, iss_path)
    print(f"[installer] Instalador gerado com sucesso: {installer_path}")
    print_hwid_instructions()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
