"""Application bootstrap for local runs and PyInstaller builds."""

from __future__ import annotations

import os
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.database import get_database_path, initialize_database
from src.security.license_manager import LicenseCollectionError, LicenseManager, LicenseValidationError
from src.utils import find_first_asset, load_stylesheets

DEBUG_VALUES = {"1", "true", "yes", "on"}


def _consume_debug_flag(argv: list[str]) -> bool:
    debug_enabled = os.getenv("OMNICORE_DEBUG", "").strip().lower() in DEBUG_VALUES
    for debug_flag in ("--debug", "--console-debug"):
        if debug_flag in argv:
            argv.remove(debug_flag)
            debug_enabled = True
    return debug_enabled


def _log_debug_boot() -> None:
    print("[Omni-Core][DEBUG] Boas-vindas ao bootstrap do ERP.")
    print("[Omni-Core][DEBUG] Modulos carregados com sucesso: IA, FSM, Engine.")
    print(f"[Omni-Core][DEBUG] Banco ativo: {get_database_path()}")


def main(argv: list[str] | None = None) -> int:
    """Start the desktop application."""

    runtime_argv = list(sys.argv if argv is None else argv)
    debug_enabled = _consume_debug_flag(runtime_argv)

    app = QApplication(runtime_argv)

    icon_path = find_first_asset((".ico",))
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    else:
        icon = None

    try:
        license_result = LicenseManager.assert_current_machine_authorized()
    except (LicenseValidationError, LicenseCollectionError) as exc:
        if debug_enabled:
            print(f"[Omni-Core][DEBUG] Validacao HWID falhou: {exc}")
        QMessageBox.critical(
            None,
            "Licenca Omni-Core",
            "ACESSO NEGADO: Hardware n\u00e3o autorizado. Entre em contato com o suporte.",
        )
        raise SystemExit(1) from exc

    initialize_database()

    from src.core import SaleFSM, calculate_minimum_price
    from src.ia import OmniBrain
    from src.ui.main_window import MainWindow

    if debug_enabled:
        _ = (SaleFSM, OmniBrain, calculate_minimum_price)
        _log_debug_boot()
        print(
            "[Omni-Core][DEBUG] Validacao HWID concluida com sucesso: "
            f"{LicenseManager.mask_hash(license_result.current_hash)} "
            f"em {license_result.license_path}"
        )

    window = MainWindow()
    if icon is not None:
        window.setWindowIcon(icon)

    extra_stylesheet = load_stylesheets()
    if extra_stylesheet:
        window.setStyleSheet(f"{window.styleSheet()}\n\n{extra_stylesheet}")

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
