"""Main PyQt6 window for the Omni-Core ERP dashboard and operations."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.database.connection import get_database_path, initialize_database
from src.network import DEFAULT_GATEWAY_PORT, WhatsAppGatewayThread
from src.ui.dashboard_module import DashboardModuleWidget
from src.ui.product_manager import ProductManagerWidget
from src.ui.sales_module import SalesModuleWidget
from src.ui.settings_module import SettingsModuleWidget
from src.ui.style_manager import BrandingConfig, StyleManager, ThemeMode


@dataclass(frozen=True, slots=True)
class NavigationItem:
    """Sidebar navigation item."""

    key: str
    label: str


class MainWindow(QMainWindow):
    """Professional desktop shell with sidebar, white-label branding and stacked navigation."""

    def __init__(self) -> None:
        super().__init__()
        initialize_database()

        self._style_manager = StyleManager()
        self._branding: BrandingConfig = self._style_manager.load_branding()
        self._theme_mode = self._style_manager.resolve_theme_mode()

        self.setWindowTitle(self._branding.app_name)
        self.resize(1400, 880)
        self.setMinimumSize(1220, 760)

        self._nav_buttons: dict[str, QPushButton] = {}
        self._stack: QStackedWidget | None = None
        self._pages: dict[str, QWidget] = {}
        self._theme_toggle_button: QPushButton | None = None
        self._gateway_thread: WhatsAppGatewayThread | None = None
        self._integration_led: QLabel | None = None
        self._integration_status_label: QLabel | None = None
        self._dashboard_page = DashboardModuleWidget()
        self._product_manager = ProductManagerWidget()
        self._sales_module = SalesModuleWidget()
        self._settings_module = SettingsModuleWidget()

        self._product_manager.products_changed.connect(self._on_products_changed)
        self._sales_module.sale_completed.connect(self._on_sale_completed)

        self._build_ui()
        self._build_status_bar()
        self._apply_theme(self._theme_mode, persist=False)
        self._switch_page("dashboard")
        self._start_gateway()

    def _build_ui(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_sidebar())
        shell.addWidget(self._build_stack(), stretch=1)

        self.setCentralWidget(root)

    def _build_status_bar(self) -> None:
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)

        self._integration_led = QLabel()
        self._integration_led.setFixedSize(12, 12)
        self._integration_status_label = QLabel()
        self._integration_status_label.setObjectName("sidebarFootnote")

        status_bar.addPermanentWidget(self._integration_led)
        status_bar.addPermanentWidget(self._integration_status_label)
        self._update_gateway_status(
            False,
            f"Servidor de integracao iniciando na porta {DEFAULT_GATEWAY_PORT}.",
        )

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(288)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(24, 24, 22, 24)
        layout.setSpacing(16)

        layout.addWidget(self._build_brand_block())

        self._theme_toggle_button = QPushButton()
        self._theme_toggle_button.setObjectName("secondaryButton")
        self._theme_toggle_button.setCheckable(True)
        self._theme_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_toggle_button.clicked.connect(self._on_theme_toggle_clicked)
        layout.addWidget(self._theme_toggle_button)

        for item in (
            NavigationItem("dashboard", "Dashboard"),
            NavigationItem("products", "Produtos"),
            NavigationItem("sales", "Vendas"),
            NavigationItem("settings", "Configuracoes"),
        ):
            button = QPushButton(item.label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, key=item.key: self._switch_page(key))
            self._nav_buttons[item.key] = button
            layout.addWidget(button)

        layout.addItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        database_label = QLabel(f"SQLite local\n{get_database_path()}")
        database_label.setObjectName("sidebarFootnote")
        database_label.setWordWrap(True)
        layout.addWidget(database_label)

        support_label = QLabel(f"Suporte tecnico por: {self._branding.support_provider}")
        support_label.setObjectName("providerFootnote")
        support_label.setWordWrap(True)
        layout.addWidget(support_label)

        return sidebar

    def _build_brand_block(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        logo_label = QLabel()
        logo_label.setObjectName("logoLabel")
        logo_label.setPixmap(self._style_manager.build_logo_pixmap(QSize(76, 76)))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        logo_label.setFixedSize(80, 80)

        title_column = QVBoxLayout()
        title_column.setSpacing(4)

        brand = QLabel(self._branding.app_name)
        brand.setObjectName("brandTitle")
        brand.setWordWrap(True)

        subtitle = QLabel(self._branding.edition)
        subtitle.setObjectName("brandSubtitle")
        subtitle.setWordWrap(True)

        title_column.addWidget(brand)
        title_column.addWidget(subtitle)
        title_column.addStretch(1)

        layout.addWidget(logo_label, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_column, stretch=1)
        return container

    def _build_stack(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._pages = {
            "dashboard": self._dashboard_page,
            "products": self._product_manager,
            "sales": self._sales_module,
            "settings": self._settings_module,
        }
        for page in self._pages.values():
            self._stack.addWidget(page)

        layout.addWidget(self._stack)
        return container

    def _switch_page(self, key: str) -> None:
        if self._stack is None:
            return

        target = self._pages[key]
        self._stack.setCurrentWidget(target)

        for button_key, button in self._nav_buttons.items():
            button.setChecked(button_key == key)

        if key == "dashboard":
            self._dashboard_page.refresh_dashboard()

    def _on_products_changed(self) -> None:
        self._dashboard_page.refresh_dashboard()
        self._sales_module.refresh_products()

    def _on_sale_completed(self) -> None:
        self._dashboard_page.refresh_dashboard()
        self._product_manager.refresh_products()

    def _on_theme_toggle_clicked(self, checked: bool) -> None:
        mode = ThemeMode.DARK if checked else ThemeMode.LIGHT
        self._apply_theme(mode, persist=True)

    def _apply_theme(self, mode: ThemeMode, *, persist: bool) -> None:
        palette = self._style_manager.build_palette(mode)
        stylesheet = self._style_manager.build_stylesheet(palette)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet(stylesheet)

        self._dashboard_page.apply_palette(
            primary=palette.primary,
            secondary=palette.secondary,
            chart_surface=palette.chart_surface,
            chart_text=palette.chart_text,
            chart_muted=palette.chart_muted,
            chart_grid=palette.chart_grid,
            chart_annotation_background=palette.chart_annotation_background,
            chart_annotation_border=palette.border,
        )

        self._theme_mode = mode
        self._sync_theme_toggle()
        if persist:
            self._style_manager.save_theme_mode(mode)

    def _sync_theme_toggle(self) -> None:
        if self._theme_toggle_button is None:
            return

        is_dark = self._theme_mode is ThemeMode.DARK
        self._theme_toggle_button.blockSignals(True)
        self._theme_toggle_button.setChecked(is_dark)
        self._theme_toggle_button.setText("Tema: Escuro" if is_dark else "Tema: Claro")
        self._theme_toggle_button.blockSignals(False)

    def _start_gateway(self) -> None:
        if self._gateway_thread is not None and self._gateway_thread.isRunning():
            return

        self._gateway_thread = WhatsAppGatewayThread(parent=self)
        self._gateway_thread.status_changed.connect(self._update_gateway_status)
        self._gateway_thread.start()

    def _stop_gateway(self) -> None:
        if self._gateway_thread is None:
            return

        gateway = self._gateway_thread
        self._gateway_thread = None
        try:
            gateway.status_changed.disconnect(self._update_gateway_status)
        except TypeError:
            pass
        gateway.stop()
        if gateway.isRunning():
            gateway.wait(5000)

    def _update_gateway_status(self, online: bool, message: str) -> None:
        if self._integration_led is None or self._integration_status_label is None:
            return

        led_color = "#3ad17f" if online else "#f25555"
        border_color = "#1f8a4c" if online else "#8f1f1f"
        self._integration_led.setStyleSheet(
            "border-radius: 6px; "
            f"background-color: {led_color}; "
            f"border: 1px solid {border_color};"
        )
        self._integration_status_label.setText(
            "Servidor de Integracao: Online" if online else "Servidor de Integracao: Offline"
        )
        self._integration_status_label.setToolTip(message)
        self.statusBar().showMessage(message, 10000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_gateway()
        super().closeEvent(event)


def run() -> int:
    """Start the desktop application."""

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
