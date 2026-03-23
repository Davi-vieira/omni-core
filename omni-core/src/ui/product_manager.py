"""Product management screen for Omni-Core ERP."""

from __future__ import annotations

from decimal import Decimal

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from src.core import (
    EngineError,
    deactivate_product,
    list_products,
    register_product,
    update_product,
)
from src.domain.models import Product


def _format_currency(value: Decimal) -> str:
    normalized = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {normalized}"


class ProductManagerWidget(QWidget):
    """Manage product CRUD operations without embedding business rules in the UI."""

    products_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_product_id: int | None = None
        self._build_ui()
        self.refresh_products()

    def refresh_products(self) -> None:
        """Reload the product table from persistence."""

        products = list_products(
            only_active=False,
            search_term=self._search_input.text().strip(),
        )
        self._table.setRowCount(len(products))

        for row_index, product in enumerate(products):
            values = (
                str(product.id or ""),
                product.sku,
                product.nome,
                product.categoria,
                _format_currency(product.preco_custo),
                _format_currency(product.preco_tabela),
                str(product.estoque_atual),
                f"{(product.margem_ia * Decimal('100')):.2f}%",
                f"{(product.margem_minima * Decimal('100')):.2f}%",
                "Sim" if product.ativo else "Nao",
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(256, product.id)
                self._table.setItem(row_index, column_index, item)

        self._table.resizeRowsToContents()
        self._status_label.setText(f"{len(products)} produto(s) carregado(s).")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(18)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_box = QVBoxLayout()
        title_box.setSpacing(6)

        title = QLabel("Produtos")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Cadastre, edite e desative produtos. A persistencia continua validada pelo Motor de Regras."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)

        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Buscar por nome, SKU ou categoria")
        self._search_input.textChanged.connect(self.refresh_products)

        header_layout.addLayout(title_box, stretch=1)
        header_layout.addWidget(self._search_input)

        root_layout.addWidget(header)

        content_layout = QGridLayout()
        content_layout.setHorizontalSpacing(18)
        content_layout.setVerticalSpacing(18)

        content_layout.addWidget(self._build_table_panel(), 0, 0)
        content_layout.addWidget(self._build_form_panel(), 0, 1)
        content_layout.setColumnStretch(0, 3)
        content_layout.setColumnStretch(1, 2)

        root_layout.addLayout(content_layout)

        self._status_label = QLabel("Pronto para operacao.")
        self._status_label.setObjectName("statusText")
        root_layout.addWidget(self._status_label)

    def _build_table_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        panel_title = QLabel("Catalogo de produtos")
        panel_title.setObjectName("sectionTitle")
        layout.addWidget(panel_title)

        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            (
                "ID",
                "SKU",
                "Nome",
                "Categoria",
                "Custo",
                "Tabela",
                "Estoque",
                "Margem IA",
                "Margem Min.",
                "Ativo",
            )
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._load_selected_product)
        layout.addWidget(self._table)

        return panel

    def _build_form_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        panel_title = QLabel("Formulario de produto")
        panel_title.setObjectName("sectionTitle")
        layout.addWidget(panel_title)

        form = QFormLayout()
        form.setSpacing(12)

        self._sku_input = QLineEdit()
        self._nome_input = QLineEdit()
        self._categoria_input = QLineEdit()
        self._descricao_input = QTextEdit()
        self._descricao_input.setFixedHeight(88)

        self._preco_custo_input = QDoubleSpinBox()
        self._preco_tabela_input = QDoubleSpinBox()
        for widget in (self._preco_custo_input, self._preco_tabela_input):
            widget.setDecimals(2)
            widget.setMaximum(999999999.99)
            widget.setPrefix("R$ ")

        self._estoque_input = QSpinBox()
        self._estoque_input.setMaximum(1_000_000)

        self._margem_ia_input = QDoubleSpinBox()
        self._margem_minima_input = QDoubleSpinBox()
        for widget in (self._margem_ia_input, self._margem_minima_input):
            widget.setDecimals(2)
            widget.setRange(0.0, 100.0)
            widget.setSuffix("%")

        form.addRow("SKU", self._sku_input)
        form.addRow("Nome", self._nome_input)
        form.addRow("Categoria", self._categoria_input)
        form.addRow("Descricao", self._descricao_input)
        form.addRow("Preco de Custo", self._preco_custo_input)
        form.addRow("Preco de Tabela", self._preco_tabela_input)
        form.addRow("Estoque Atual", self._estoque_input)
        form.addRow("Margem IA", self._margem_ia_input)
        form.addRow("Margem Minima", self._margem_minima_input)

        layout.addLayout(form)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        new_button = QPushButton("Novo")
        new_button.clicked.connect(self._reset_form)

        save_button = QPushButton("Salvar")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save_product)

        delete_button = QPushButton("Excluir")
        delete_button.clicked.connect(self._delete_product)

        buttons_layout.addWidget(new_button)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(delete_button)
        layout.addLayout(buttons_layout)

        return panel

    def _load_selected_product(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return

        product_id = items[0].data(256)
        if product_id is None:
            return

        product = next(
            (item for item in list_products(only_active=False) if item.id == int(product_id)),
            None,
        )
        if product is None:
            return

        self._current_product_id = product.id
        self._sku_input.setText(product.sku)
        self._nome_input.setText(product.nome)
        self._categoria_input.setText(product.categoria)
        self._descricao_input.setPlainText(product.descricao)
        self._preco_custo_input.setValue(float(product.preco_custo))
        self._preco_tabela_input.setValue(float(product.preco_tabela))
        self._estoque_input.setValue(product.estoque_atual)
        self._margem_ia_input.setValue(float(product.margem_ia * Decimal("100")))
        self._margem_minima_input.setValue(float(product.margem_minima * Decimal("100")))
        self._status_label.setText(f"Produto selecionado: {product.nome}")

    def _build_product_from_form(self) -> Product:
        return Product(
            id=self._current_product_id,
            sku=self._sku_input.text(),
            nome=self._nome_input.text(),
            categoria=self._categoria_input.text(),
            descricao=self._descricao_input.toPlainText(),
            preco_custo=Decimal(str(self._preco_custo_input.value())),
            preco_tabela=Decimal(str(self._preco_tabela_input.value())),
            margem_ia=Decimal(str(self._margem_ia_input.value() / 100)),
            margem_minima=Decimal(str(self._margem_minima_input.value() / 100)),
            estoque_atual=int(self._estoque_input.value()),
        )

    def _save_product(self) -> None:
        try:
            product = self._build_product_from_form()
            if product.id is None:
                saved = register_product(product)
                action = "cadastrado"
            else:
                saved = update_product(product)
                action = "atualizado"
        except EngineError as exc:
            QMessageBox.warning(self, "Validacao", str(exc))
            return

        self._current_product_id = saved.id
        self._status_label.setText(f"Produto {action}: {saved.nome}")
        self.refresh_products()
        self.products_changed.emit()

    def _delete_product(self) -> None:
        if self._current_product_id is None:
            QMessageBox.information(self, "Produtos", "Selecione um produto para excluir.")
            return

        reason, accepted = QInputDialog.getText(
            self,
            "Motivo da exclusao",
            "Informe o motivo da exclusao logica do produto:",
        )
        if not accepted:
            return

        try:
            product = deactivate_product(self._current_product_id, reason)
        except EngineError as exc:
            QMessageBox.warning(self, "Exclusao", str(exc))
            return

        QMessageBox.information(
            self,
            "Produto desativado",
            f"O produto '{product.nome}' foi desativado com sucesso.",
        )
        self._reset_form()
        self.refresh_products()
        self.products_changed.emit()

    def _reset_form(self) -> None:
        self._current_product_id = None
        self._sku_input.clear()
        self._nome_input.clear()
        self._categoria_input.clear()
        self._descricao_input.clear()
        self._preco_custo_input.setValue(0.0)
        self._preco_tabela_input.setValue(0.0)
        self._estoque_input.setValue(0)
        self._margem_ia_input.setValue(0.0)
        self._margem_minima_input.setValue(0.0)
        self._table.clearSelection()
        self._status_label.setText("Formulario limpo para novo cadastro.")
