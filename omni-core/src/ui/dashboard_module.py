"""Analytics dashboard module for Omni-Core ERP."""

from __future__ import annotations

from decimal import Decimal

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.database import DashboardRepository, get_connection, get_database_path
from src.database.repositories import AnalyticsPoint, DashboardAnalyticsSnapshot, StockSuggestionItem


def _format_currency(value: Decimal) -> str:
    normalized = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {normalized}"


def _format_percentage(value: Decimal) -> str:
    normalized = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{normalized}%"


class AnalyticsWorker(QThread):
    """Fetch dashboard analytics in a background thread."""

    snapshot_ready = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        *,
        days: int = 7,
        critical_stock_threshold: int = 3,
    ) -> None:
        super().__init__()
        self._days = days
        self._critical_stock_threshold = critical_stock_threshold

    def run(self) -> None:
        connection = None
        try:
            connection = get_connection()
            repository = DashboardRepository(connection)
            snapshot = repository.fetch_analytics_snapshot(
                days=self._days,
                critical_stock_threshold=self._critical_stock_threshold,
            )
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        finally:
            if connection is not None:
                connection.close()

        self.snapshot_ready.emit(snapshot)


class AnalyticsCard(QFrame):
    """Visual KPI card for the analytics dashboard."""

    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("analyticsCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        self._accent_bar = QFrame()
        self._accent_bar.setFixedHeight(4)
        self.set_accent(accent)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("analyticsCardTitle")

        self._value_label = QLabel("--")
        self._value_label.setObjectName("analyticsCardValue")

        self._hint_label = QLabel("Aguardando dados do SQLite...")
        self._hint_label.setObjectName("analyticsCardHint")
        self._hint_label.setWordWrap(True)

        layout.addWidget(self._accent_bar)
        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._hint_label)

    def update_content(self, value: str, hint: str) -> None:
        """Update the card body with fresh analytics values."""

        self._value_label.setText(value)
        self._hint_label.setText(hint)

    def set_accent(self, accent: str) -> None:
        """Update the accent strip used by the KPI card."""

        self._accent_bar.setStyleSheet(f"background-color: {accent}; border-radius: 2px;")


class AnalyticsChartCanvas(FigureCanvasQTAgg):
    """Reusable matplotlib canvas styled for the Dark Pro dashboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        figure = Figure(figsize=(5.4, 3.0), facecolor="#111827")
        self._axes = figure.add_subplot(111)
        super().__init__(figure)
        self.setParent(parent)
        self.figure.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.18)

    def plot_series(
        self,
        *,
        title: str,
        subtitle: str,
        points: list[AnalyticsPoint],
        accent: str,
        currency: bool,
        facecolor: str,
        text_color: str,
        muted_color: str,
        grid_color: str,
        annotation_background: str,
        annotation_border: str,
        fill: bool = False,
    ) -> None:
        """Render a dark-themed line chart for the provided series."""

        axes = self._axes
        axes.clear()
        self.figure.set_facecolor(facecolor)
        axes.set_facecolor(facecolor)

        labels = [point.label for point in points]
        values = [float(point.value) for point in points]
        positions = list(range(len(points)))

        axes.plot(
            positions,
            values,
            color=accent,
            linewidth=2.8,
            marker="o",
            markersize=5.5,
            markerfacecolor="#0f172a",
            markeredgewidth=1.6,
            markeredgecolor=accent,
        )

        if fill:
            axes.fill_between(positions, values, color=accent, alpha=0.18)

        axes.set_title(title, color=text_color, fontsize=13, fontweight="bold", loc="left", pad=16)
        axes.text(
            0.0,
            1.02,
            subtitle,
            transform=axes.transAxes,
            color=muted_color,
            fontsize=9,
        )
        axes.grid(axis="y", color=grid_color, alpha=0.45, linewidth=0.8)

        for spine_name in ("top", "right"):
            axes.spines[spine_name].set_visible(False)
        axes.spines["left"].set_color(grid_color)
        axes.spines["bottom"].set_color(grid_color)

        axes.tick_params(axis="x", colors=muted_color, labelsize=9)
        axes.tick_params(axis="y", colors=muted_color, labelsize=9)
        axes.set_xticks(positions)
        axes.set_xticklabels(labels)

        max_value = max(values) if values else 0.0
        axes.set_ylim(bottom=0, top=max(max_value * 1.18, 1.0))
        axes.margins(x=0.03)

        if values:
            last_value = Decimal(str(values[-1])).quantize(Decimal("0.01"))
            marker_text = _format_currency(last_value) if currency else f"{last_value:.2f}"
            axes.annotate(
                marker_text,
                xy=(positions[-1], values[-1]),
                xytext=(0, -24),
                textcoords="offset points",
                ha="center",
                color=text_color,
                fontsize=9,
                bbox={
                    "boxstyle": "round,pad=0.25",
                    "facecolor": annotation_background,
                    "edgecolor": annotation_border,
                    "linewidth": 0.8,
                },
            )

        self.draw_idle()


class StockHeatRow(QFrame):
    """Visual row for critical-stock suggestions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockHeatRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        self._severity_badge = QLabel("!")
        self._severity_badge.setObjectName("stockSeverityBadge")
        self._severity_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._severity_badge.setFixedSize(28, 28)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)

        self._name_label = QLabel("--")
        self._name_label.setObjectName("stockHeatName")
        self._category_label = QLabel("--")
        self._category_label.setObjectName("stockHeatCategory")

        text_column.addWidget(self._name_label)
        text_column.addWidget(self._category_label)

        meta_column = QVBoxLayout()
        meta_column.setSpacing(3)

        self._stock_label = QLabel("--")
        self._stock_label.setObjectName("stockHeatMeta")
        self._suggestion_label = QLabel("--")
        self._suggestion_label.setObjectName("stockHeatMeta")
        self._stock_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._suggestion_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        meta_column.addWidget(self._stock_label)
        meta_column.addWidget(self._suggestion_label)

        layout.addWidget(self._severity_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_column, stretch=1)
        layout.addLayout(meta_column)

    def bind(self, item: StockSuggestionItem, max_suggestions: int) -> None:
        """Apply the product analytics data to the visual row."""

        self._name_label.setText(item.nome)
        self._category_label.setText(item.categoria or "Sem categoria")
        self._stock_label.setText(f"Estoque: {item.estoque_atual}")
        self._suggestion_label.setText(f"Sugestoes IA: {item.suggestion_count}")

        suggestion_ratio = item.suggestion_count / max(max_suggestions, 1)
        stock_ratio = 1.0 if item.estoque_atual <= 0 else min(item.estoque_atual / 3.0, 1.0)
        red = int(120 + (90 * suggestion_ratio))
        green = int(52 + (65 * stock_ratio))
        blue = int(62 + (35 * stock_ratio))
        background = f"rgba({red}, {green}, {blue}, 0.28)"
        border = f"rgb({min(red + 35, 255)}, {min(green + 20, 255)}, {min(blue + 20, 255)})"

        self.setStyleSheet(
            f"""
            QFrame#stockHeatRow {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: 16px;
            }}
            QLabel#stockSeverityBadge {{
                background-color: rgba(248, 113, 113, 0.22);
                color: #fecaca;
                border-radius: 14px;
                border: 1px solid rgba(248, 113, 113, 0.35);
                font-weight: 700;
            }}
            """
        )


class DashboardModuleWidget(QWidget):
    """Dark-themed analytics dashboard backed by the SQLite persistence layer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("analyticsPage")
        self._worker: AnalyticsWorker | None = None
        self._stock_rows: list[StockHeatRow] = []
        self._cards: dict[str, AnalyticsCard] = {}
        self._loading_label: QLabel | None = None
        self._status_label: QLabel | None = None
        self._last_snapshot: DashboardAnalyticsSnapshot | None = None
        self._revenue_accent = "#38bdf8"
        self._ticket_accent = "#34d399"
        self._chart_surface = "#111827"
        self._chart_text = "#f8fafc"
        self._chart_muted = "#94a3b8"
        self._chart_grid = "#334155"
        self._chart_annotation_background = "#0f172a"
        self._chart_annotation_border = "#1e293b"

        self._build_ui()
        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        """Refresh analytics asynchronously from the local SQLite database."""

        if self._worker is not None and self._worker.isRunning():
            return

        self._set_loading(True, "Sincronizando analytics com o SQLite local...")

        self._worker = AnalyticsWorker(days=7, critical_stock_threshold=3)
        self._worker.snapshot_ready.connect(self._on_snapshot_ready)
        self._worker.error_occurred.connect(self._on_snapshot_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def apply_palette(
        self,
        *,
        primary: str,
        secondary: str,
        chart_surface: str,
        chart_text: str,
        chart_muted: str,
        chart_grid: str,
        chart_annotation_background: str,
        chart_annotation_border: str,
    ) -> None:
        """Update chart colors when the application theme changes."""

        self._revenue_accent = primary
        self._ticket_accent = secondary
        self._chart_surface = chart_surface
        self._chart_text = chart_text
        self._chart_muted = chart_muted
        self._chart_grid = chart_grid
        self._chart_annotation_background = chart_annotation_background
        self._chart_annotation_border = chart_annotation_border
        self._cards["vendas_hoje"].set_accent(primary)
        self._cards["atendimentos_ia"].set_accent(secondary)
        self._cards["conversao_ia"].set_accent(primary)

        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(28, 28, 28, 28)
        root_layout.setSpacing(20)

        hero = QFrame()
        hero.setObjectName("analyticsHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 24, 24, 24)
        hero_layout.setSpacing(18)

        title_column = QVBoxLayout()
        title_column.setSpacing(8)

        title = QLabel("Analytics de Vendas e IA")
        title.setObjectName("analyticsHeadline")
        subtitle = QLabel(
            "Faturamento, conversao e prescricao da IA extraidos do SQLite em tempo real, "
            "sem bypassar a FSM nem o Motor de Regras."
        )
        subtitle.setObjectName("analyticsSubtitle")
        subtitle.setWordWrap(True)

        title_column.addWidget(title)
        title_column.addWidget(subtitle)

        control_column = QVBoxLayout()
        control_column.setSpacing(10)

        self._loading_label = QLabel("Analytics prontos.")
        self._loading_label.setObjectName("analyticsStatusPill")

        refresh_button = QPushButton("Atualizar analytics")
        refresh_button.setObjectName("analyticsPrimaryButton")
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.clicked.connect(self.refresh_dashboard)

        control_column.addWidget(self._loading_label, alignment=Qt.AlignmentFlag.AlignRight)
        control_column.addWidget(refresh_button, alignment=Qt.AlignmentFlag.AlignRight)
        control_column.addStretch(1)

        hero_layout.addLayout(title_column, stretch=1)
        hero_layout.addLayout(control_column)
        root_layout.addWidget(hero)

        cards_grid = QGridLayout()
        cards_grid.setHorizontalSpacing(16)
        cards_grid.setVerticalSpacing(16)

        self._cards["vendas_hoje"] = AnalyticsCard("Vendas Hoje (R$)", "#38bdf8")
        self._cards["atendimentos_ia"] = AnalyticsCard("Atendimentos IA", "#f59e0b")
        self._cards["conversao_ia"] = AnalyticsCard("Conversao (%)", "#34d399")

        cards_grid.addWidget(self._cards["vendas_hoje"], 0, 0)
        cards_grid.addWidget(self._cards["atendimentos_ia"], 0, 1)
        cards_grid.addWidget(self._cards["conversao_ia"], 0, 2)
        root_layout.addLayout(cards_grid)

        body_grid = QGridLayout()
        body_grid.setHorizontalSpacing(18)
        body_grid.setVerticalSpacing(18)

        self._revenue_chart = AnalyticsChartCanvas(self)
        self._ticket_chart = AnalyticsChartCanvas(self)

        body_grid.addWidget(
            self._build_chart_panel(
                title="Faturamento diario via IA",
                caption="Receita consolidada das vendas concluidas com origem IA nos ultimos 7 dias.",
                canvas=self._revenue_chart,
            ),
            0,
            0,
            1,
            2,
        )
        body_grid.addWidget(
            self._build_stock_panel(),
            0,
            2,
            2,
            1,
        )
        body_grid.addWidget(
            self._build_chart_panel(
                title="Ticket medio via IA",
                caption="Media diaria das vendas fechadas pela camada de sugestao da IA.",
                canvas=self._ticket_chart,
            ),
            1,
            0,
            1,
            2,
        )
        body_grid.setColumnStretch(0, 3)
        body_grid.setColumnStretch(1, 3)
        body_grid.setColumnStretch(2, 2)
        root_layout.addLayout(body_grid, stretch=1)

        self._status_label = QLabel(f"Fonte ativa: {get_database_path()}")
        self._status_label.setObjectName("analyticsFootnote")
        root_layout.addWidget(self._status_label)

    def _build_chart_panel(
        self,
        *,
        title: str,
        caption: str,
        canvas: AnalyticsChartCanvas,
    ) -> QWidget:
        panel = QFrame()
        panel.setObjectName("analyticsPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("analyticsPanelTitle")
        caption_label = QLabel(caption)
        caption_label.setObjectName("analyticsPanelCaption")
        caption_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(caption_label)
        layout.addWidget(canvas, stretch=1)
        return panel

    def _build_stock_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("analyticsPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Mapa de Calor de Estoque")
        title.setObjectName("analyticsPanelTitle")
        caption = QLabel(
            "Top 5 produtos com estoque critico que a IA mais sugeriu hoje. "
            "Quanto mais quente a linha, maior o risco operacional."
        )
        caption.setObjectName("analyticsPanelCaption")
        caption.setWordWrap(True)

        self._stock_summary_label = QLabel("Aguardando leitura da auditoria...")
        self._stock_summary_label.setObjectName("analyticsStockSummary")
        self._stock_summary_label.setWordWrap(True)

        self._stock_container = QVBoxLayout()
        self._stock_container.setSpacing(10)

        empty_state = QLabel("Nenhum alerta de estoque critico carregado.")
        empty_state.setObjectName("analyticsEmptyState")
        self._stock_container.addWidget(empty_state)
        self._stock_container.addStretch(1)

        layout.addWidget(title)
        layout.addWidget(caption)
        layout.addWidget(self._stock_summary_label)
        layout.addLayout(self._stock_container, stretch=1)
        return panel

    def _on_snapshot_ready(self, snapshot: object) -> None:
        if not isinstance(snapshot, DashboardAnalyticsSnapshot):
            self._on_snapshot_error("Resposta invalida ao carregar analytics.")
            return

        self._last_snapshot = snapshot
        self._render_snapshot(snapshot)

        if self._status_label is not None:
            self._status_label.setText(
                f"Fonte ativa: {get_database_path()} | Conversao IA: {_format_percentage(snapshot.conversao_ia_percentual)}"
            )
        self._set_loading(False, "Analytics atualizados com sucesso.")

    def _render_snapshot(self, snapshot: DashboardAnalyticsSnapshot) -> None:
        """Bind the analytics snapshot to cards, charts and stock alerts."""

        self._cards["vendas_hoje"].update_content(
            _format_currency(snapshot.vendas_hoje_total),
            "Receita das vendas concluidas no dia corrente.",
        )
        self._cards["atendimentos_ia"].update_content(
            str(snapshot.atendimentos_ia),
            "Sessoes unicas com interacao da IA registradas hoje.",
        )
        self._cards["conversao_ia"].update_content(
            _format_percentage(snapshot.conversao_ia_percentual),
            "Fechamentos via IA sobre os atendimentos da mesma janela.",
        )

        self._revenue_chart.plot_series(
            title="Faturamento Diario",
            subtitle="Ultimos 7 dias de vendas concluidas com origem IA",
            points=snapshot.faturamento_diario_ia,
            accent=self._revenue_accent,
            currency=True,
            facecolor=self._chart_surface,
            text_color=self._chart_text,
            muted_color=self._chart_muted,
            grid_color=self._chart_grid,
            annotation_background=self._chart_annotation_background,
            annotation_border=self._chart_annotation_border,
            fill=True,
        )
        self._ticket_chart.plot_series(
            title="Ticket Medio por Dia",
            subtitle="Media diaria de valor por venda concluida via IA",
            points=snapshot.ticket_medio_ia,
            accent=self._ticket_accent,
            currency=True,
            facecolor=self._chart_surface,
            text_color=self._chart_text,
            muted_color=self._chart_muted,
            grid_color=self._chart_grid,
            annotation_background=self._chart_annotation_background,
            annotation_border=self._chart_annotation_border,
            fill=False,
        )

        self._render_stock_heatmap(snapshot.estoque_critico_sugerido)

    def _on_snapshot_error(self, message: str) -> None:
        self._last_snapshot = None
        self._cards["vendas_hoje"].update_content("R$ 0,00", "Falha ao consultar o banco.")
        self._cards["atendimentos_ia"].update_content("--", "Revise a conexao local do SQLite.")
        self._cards["conversao_ia"].update_content("--", message or "Analytics indisponiveis.")
        self._render_stock_heatmap([])

        if self._status_label is not None:
            self._status_label.setText("Analytics indisponiveis. Operando em modo somente leitura.")
        self._set_loading(False, f"Falha ao atualizar analytics: {message}")

    def _on_worker_finished(self) -> None:
        if self._worker is None:
            return
        self._worker.deleteLater()
        self._worker = None

    def _set_loading(self, active: bool, message: str) -> None:
        if self._loading_label is None:
            return
        self._loading_label.setText(message)
        self._loading_label.setProperty("loading", active)
        self._loading_label.style().unpolish(self._loading_label)
        self._loading_label.style().polish(self._loading_label)

    def _render_stock_heatmap(self, items: list[StockSuggestionItem]) -> None:
        self._clear_stock_rows()

        if not items:
            empty_state = QLabel("Nenhum produto critico sugerido pela IA hoje.")
            empty_state.setObjectName("analyticsEmptyState")
            self._stock_container.addWidget(empty_state)
            self._stock_container.addStretch(1)
            self._stock_summary_label.setText(
                "Sem alertas de prescricao arriscada no dia. O estoque critico nao foi pressionado."
            )
            return

        max_suggestions = max(item.suggestion_count for item in items)
        total_suggestions = sum(item.suggestion_count for item in items)
        self._stock_summary_label.setText(
            f"{len(items)} produto(s) critico(s) concentraram {total_suggestions} sugestao(oes) da IA hoje."
        )

        for item in items:
            row = StockHeatRow(self)
            row.bind(item, max_suggestions=max_suggestions)
            self._stock_rows.append(row)
            self._stock_container.addWidget(row)

        self._stock_container.addStretch(1)

    def _clear_stock_rows(self) -> None:
        self._stock_rows.clear()
        while self._stock_container.count():
            child = self._stock_container.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
