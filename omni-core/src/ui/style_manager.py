"""Dynamic white-label theme and branding management for Omni-Core ERP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from src.database import SettingsRepository, get_connection, transaction
from src.utils.runtime import (
    get_bundle_data_dir,
    get_bundle_root,
    get_runtime_data_dir,
    get_runtime_root,
    load_stylesheets,
)

THEME_FILENAME = "theme.json"
LOGO_FILENAME = "logo.png"
DEFAULT_PRIMARY = "#1f6f78"
DEFAULT_SECONDARY = "#f4b266"
DEFAULT_BACKGROUND = "#0b1220"
DEFAULT_SUPPORT_PROVIDER = "Seu Nome/Empresa"
THEME_MODE_SETTING_KEY = "ui.theme_mode"


class ThemeMode(str, Enum):
    """Available visual modes for the white-label shell."""

    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class BrandingConfig:
    """Branding metadata loaded from ``theme.json``."""

    app_name: str
    edition: str
    support_provider: str


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Expanded color palette derived from the white-label configuration."""

    mode: ThemeMode
    primary: str
    secondary: str
    background: str
    page_background: str
    surface: str
    surface_alt: str
    sidebar_background: str
    sidebar_hover: str
    sidebar_text: str
    sidebar_muted: str
    border: str
    input_background: str
    input_border: str
    header_background: str
    text_primary: str
    text_secondary: str
    text_inverse: str
    success: str
    warning: str
    danger: str
    chart_surface: str
    chart_grid: str
    chart_text: str
    chart_muted: str
    chart_annotation_background: str


class StyleManager:
    """Load branding assets, manage theme persistence and build dynamic QSS."""

    def __init__(self) -> None:
        self._theme_document = self._load_theme_document()

    def load_branding(self) -> BrandingConfig:
        """Return the branding labels exposed by the shell."""

        branding = self._theme_document.get("branding", {})
        app_name = str(branding.get("app_name") or "Omni-Core ERP").strip() or "Omni-Core ERP"
        edition = str(branding.get("edition") or "Enterprise Edition").strip() or "Enterprise Edition"
        support_provider = (
            str(branding.get("support_provider") or DEFAULT_SUPPORT_PROVIDER).strip()
            or DEFAULT_SUPPORT_PROVIDER
        )
        return BrandingConfig(
            app_name=app_name,
            edition=edition,
            support_provider=support_provider,
        )

    def resolve_theme_mode(self) -> ThemeMode:
        """Resolve the active mode using DB preference first, then ``theme.json``."""

        persisted_mode = self._load_persisted_mode()
        if persisted_mode is not None:
            return persisted_mode

        theme_section = self._theme_document.get("theme", {})
        configured_mode = str(theme_section.get("mode") or ThemeMode.DARK.value).strip().lower()
        if configured_mode == ThemeMode.LIGHT.value:
            return ThemeMode.LIGHT
        return ThemeMode.DARK

    def save_theme_mode(self, mode: ThemeMode) -> None:
        """Persist the user-selected mode in SQLite."""

        with transaction() as connection:
            SettingsRepository(connection).set_value(THEME_MODE_SETTING_KEY, mode.value)

    def build_palette(self, mode: ThemeMode) -> ThemePalette:
        """Build the full semantic palette for the requested mode."""

        theme_section = self._theme_document.get("theme", {})
        primary = self._normalize_hex(str(theme_section.get("primary") or DEFAULT_PRIMARY), DEFAULT_PRIMARY)
        secondary = self._normalize_hex(str(theme_section.get("secondary") or DEFAULT_SECONDARY), DEFAULT_SECONDARY)
        background = self._normalize_hex(str(theme_section.get("background") or DEFAULT_BACKGROUND), DEFAULT_BACKGROUND)

        if mode is ThemeMode.DARK:
            page_background = background
            surface = self._mix(background, "#ffffff", 0.05)
            surface_alt = self._mix(background, "#ffffff", 0.09)
            sidebar_background = self._mix(primary, "#041015", 0.48)
            sidebar_hover = self._mix(sidebar_background, "#ffffff", 0.08)
            sidebar_text = "#e2e8f0"
            sidebar_muted = "#9fb5c3"
            border = self._mix(background, "#ffffff", 0.12)
            input_background = self._mix(background, "#ffffff", 0.035)
            input_border = self._mix(background, "#ffffff", 0.14)
            header_background = self._mix(surface, primary, 0.12)
            text_primary = "#f8fafc"
            text_secondary = "#94a3b8"
            text_inverse = "#0f172a"
            chart_surface = surface
            chart_grid = self._mix(background, "#ffffff", 0.18)
            chart_text = text_primary
            chart_muted = text_secondary
            chart_annotation_background = self._mix(background, "#000000", 0.18)
        else:
            page_background = self._mix(background, "#ffffff", 0.88)
            surface = "#ffffff"
            surface_alt = self._mix(background, "#ffffff", 0.94)
            sidebar_background = self._mix(primary, "#0f172a", 0.22)
            sidebar_hover = self._mix(sidebar_background, "#ffffff", 0.14)
            sidebar_text = "#eff6ff"
            sidebar_muted = "#dbeafe"
            border = self._mix(background, "#000000", 0.10)
            input_background = "#ffffff"
            input_border = self._mix(background, "#000000", 0.14)
            header_background = self._mix(page_background, secondary, 0.18)
            text_primary = self._mix(background, "#000000", 0.74)
            text_secondary = self._mix(background, "#000000", 0.46)
            text_inverse = "#ffffff"
            chart_surface = surface
            chart_grid = self._mix(background, "#000000", 0.12)
            chart_text = text_primary
            chart_muted = text_secondary
            chart_annotation_background = surface_alt

        return ThemePalette(
            mode=mode,
            primary=primary,
            secondary=secondary,
            background=background,
            page_background=page_background,
            surface=surface,
            surface_alt=surface_alt,
            sidebar_background=sidebar_background,
            sidebar_hover=sidebar_hover,
            sidebar_text=sidebar_text,
            sidebar_muted=sidebar_muted,
            border=border,
            input_background=input_background,
            input_border=input_border,
            header_background=header_background,
            text_primary=text_primary,
            text_secondary=text_secondary,
            text_inverse=text_inverse,
            success="#16a34a",
            warning="#f59e0b",
            danger="#dc2626",
            chart_surface=chart_surface,
            chart_grid=chart_grid,
            chart_text=chart_text,
            chart_muted=chart_muted,
            chart_annotation_background=chart_annotation_background,
        )

    def build_stylesheet(self, palette: ThemePalette) -> str:
        """Return the application QSS using the active branding palette."""

        heading_font = QFont("Segoe UI", 14, QFont.Weight.Bold)
        body_font = QFont("Segoe UI", 10)
        base_stylesheet = f"""
            QWidget {{
                background-color: {palette.page_background};
                color: {palette.text_primary};
                font-family: 'Segoe UI';
                font-size: 14px;
            }}
            QMainWindow {{
                background-color: {palette.page_background};
            }}
            QFrame#sidebar {{
                background-color: {palette.sidebar_background};
                border-right: 1px solid {self._mix(palette.sidebar_background, "#ffffff", 0.08)};
            }}
            QLabel#brandTitle {{
                color: {palette.sidebar_text};
                font-size: {heading_font.pointSize() + 8}px;
                font-weight: 700;
            }}
            QLabel#brandSubtitle {{
                color: {palette.sidebar_muted};
                font-size: {body_font.pointSize()}px;
            }}
            QLabel#sidebarFootnote,
            QLabel#providerFootnote {{
                color: {palette.sidebar_muted};
                font-size: 12px;
                line-height: 1.4;
            }}
            QLabel#providerFootnote {{
                font-weight: 700;
            }}
            QLabel#logoLabel {{
                background-color: transparent;
            }}
            QPushButton#navButton {{
                text-align: left;
                padding: 12px 14px;
                border-radius: 12px;
                border: 1px solid transparent;
                font-size: 14px;
                background-color: transparent;
                color: {palette.sidebar_text};
            }}
            QPushButton#navButton:hover,
            QPushButton#secondaryButton:hover {{
                background-color: {palette.sidebar_hover};
            }}
            QPushButton#navButton:checked {{
                background-color: {palette.secondary};
                color: {palette.text_inverse};
                font-weight: 700;
            }}
            QPushButton#secondaryButton {{
                text-align: left;
                padding: 10px 14px;
                border-radius: 12px;
                border: 1px solid {self._mix(palette.sidebar_text, "#ffffff", 0.12)};
                background-color: transparent;
                color: {palette.sidebar_text};
                font-weight: 600;
            }}
            QLabel#pageTitle {{
                font-size: 26px;
                font-weight: 700;
                color: {palette.text_primary};
            }}
            QLabel#pageSubtitle {{
                color: {palette.text_secondary};
                font-size: 14px;
                line-height: 1.45;
            }}
            QPushButton#primaryButton,
            QPushButton#analyticsPrimaryButton {{
                background-color: {palette.primary};
                color: {palette.text_inverse};
                border: none;
                border-radius: 12px;
                padding: 12px 18px;
                font-weight: 700;
            }}
            QPushButton#primaryButton:hover,
            QPushButton#analyticsPrimaryButton:hover {{
                background-color: {self._mix(palette.primary, "#000000", 0.12)};
            }}
            QFrame#contentPanel,
            QFrame#summaryPanel,
            QFrame#analyticsHero,
            QFrame#analyticsPanel,
            QFrame#analyticsCard {{
                background-color: {palette.surface};
                border: 1px solid {palette.border};
                border-radius: 18px;
            }}
            QFrame#analyticsHero {{
                background-color: {palette.header_background};
                border-radius: 22px;
            }}
            QLabel#sectionTitle,
            QLabel#analyticsPanelTitle {{
                font-size: 18px;
                font-weight: 700;
                color: {palette.text_primary};
            }}
            QLabel#statusText,
            QLabel#analyticsStatusPill {{
                font-size: 13px;
                color: {palette.primary};
                font-weight: 700;
            }}
            QLabel#analyticsStatusPill {{
                background-color: {self._mix(palette.primary, palette.surface, 0.80)};
                border: 1px solid {self._mix(palette.primary, palette.surface, 0.62)};
                border-radius: 12px;
                padding: 8px 12px;
            }}
            QLabel#analyticsStatusPill[loading="true"] {{
                color: {palette.warning};
                background-color: {self._mix(palette.warning, palette.surface, 0.82)};
                border: 1px solid {self._mix(palette.warning, palette.surface, 0.64)};
            }}
            QLabel#analyticsHeadline,
            QLabel#analyticsCardValue,
            QLabel#stockHeatName {{
                color: {palette.text_primary};
            }}
            QLabel#analyticsSubtitle,
            QLabel#analyticsPanelCaption,
            QLabel#analyticsStockSummary,
            QLabel#analyticsCardHint,
            QLabel#analyticsFootnote,
            QLabel#analyticsCardTitle,
            QLabel#stockHeatCategory,
            QLabel#stockHeatMeta {{
                color: {palette.text_secondary};
            }}
            QLabel#thinkingLabel {{
                color: {palette.primary};
                font-weight: 700;
            }}
            QLabel#manualModeLabel {{
                color: {palette.danger};
                font-weight: 700;
            }}
            QTextEdit#chatHistory {{
                background-color: {palette.input_background};
                border: 1px solid {palette.input_border};
                border-radius: 12px;
                padding: 12px;
            }}
            QTableWidget,
            QLineEdit,
            QTextEdit,
            QSpinBox,
            QDoubleSpinBox {{
                background-color: {palette.input_background};
                color: {palette.text_primary};
                border: 1px solid {palette.input_border};
                border-radius: 10px;
                padding: 6px 8px;
                selection-background-color: {palette.primary};
                selection-color: {palette.text_inverse};
            }}
            QHeaderView::section {{
                background-color: {palette.surface_alt};
                color: {palette.text_primary};
                border: none;
                padding: 8px;
                font-weight: 600;
            }}
            QTableCornerButton::section {{
                background-color: {palette.surface_alt};
                border: none;
            }}
            QPushButton {{
                background-color: {palette.surface_alt};
                color: {palette.text_primary};
                border: 1px solid {palette.border};
                border-radius: 10px;
                padding: 10px 14px;
            }}
            QPushButton:hover {{
                background-color: {self._mix(palette.surface_alt, palette.primary, 0.10)};
            }}
            QPushButton:disabled {{
                color: {self._mix(palette.text_secondary, palette.surface, 0.25)};
                background-color: {self._mix(palette.surface, "#808080", 0.08)};
                border-color: {self._mix(palette.border, palette.surface, 0.3)};
            }}
            QLabel#analyticsEmptyState {{
                color: {palette.text_secondary};
                font-size: 13px;
                padding: 10px 0;
            }}
        """
        extra_stylesheets = load_stylesheets()
        if extra_stylesheets.strip():
            return f"{base_stylesheet}\n\n{extra_stylesheets}"
        return base_stylesheet

    def build_logo_pixmap(self, size: QSize) -> QPixmap:
        """Return the client logo if available, otherwise the Omni-Core default mark."""

        logo_path = self._resolve_logo_path()
        if logo_path is not None:
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return self._build_default_logo_pixmap(size)

    def _load_theme_document(self) -> dict[str, object]:
        for candidate in (get_runtime_root() / THEME_FILENAME, get_bundle_root() / THEME_FILENAME):
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def _load_persisted_mode(self) -> ThemeMode | None:
        connection = None
        try:
            connection = get_connection()
            value = SettingsRepository(connection).get_value(THEME_MODE_SETTING_KEY)
        except Exception:
            return None
        finally:
            if connection is not None:
                connection.close()

        if value == ThemeMode.LIGHT.value:
            return ThemeMode.LIGHT
        if value == ThemeMode.DARK.value:
            return ThemeMode.DARK
        return None

    @staticmethod
    def _resolve_logo_path() -> Path | None:
        for candidate in (
            get_runtime_data_dir() / LOGO_FILENAME,
            get_bundle_data_dir() / LOGO_FILENAME,
        ):
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _build_default_logo_pixmap(size: QSize) -> QPixmap:
        width = max(size.width(), 96)
        height = max(size.height(), 96)
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(pixmap.rect().adjusted(4, 4, -4, -4))
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#1f6f78"))
        gradient.setColorAt(1.0, QColor("#f4b266"))

        frame_path = QPainterPath()
        frame_path.addRoundedRect(rect, 24, 24)
        painter.fillPath(frame_path, gradient)

        pen = QPen(QColor(255, 255, 255, 70))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 24, 24)

        painter.setPen(QColor("#f8fafc"))
        font = QFont("Segoe UI", max(16, min(width, height) // 3), QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "OC")
        painter.end()

        return pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _normalize_hex(value: str, fallback: str) -> str:
        color = QColor(value)
        if not color.isValid():
            color = QColor(fallback)
        return color.name(QColor.NameFormat.HexRgb)

    @staticmethod
    def _mix(source: str, target: str, ratio: float) -> str:
        ratio = max(0.0, min(1.0, ratio))
        source_color = QColor(source)
        target_color = QColor(target)
        red = round(source_color.red() + (target_color.red() - source_color.red()) * ratio)
        green = round(source_color.green() + (target_color.green() - source_color.green()) * ratio)
        blue = round(source_color.blue() + (target_color.blue() - source_color.blue()) * ratio)
        return QColor(red, green, blue).name(QColor.NameFormat.HexRgb)
