"""Sales operation screen for Omni-Core ERP."""

from __future__ import annotations

from decimal import Decimal
from html import escape
from uuid import uuid4

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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
)

from src.core import (
    EngineError,
    FSMError,
    MissingProposalError,
    ProductNotFoundError,
    SaleFSM,
    SaleState,
    build_pricing_suggestion,
    list_products,
)
from src.domain.models import NegotiationStatus, OriginType, Product, SaleProposal
from src.ia import BrainError, BrainIntent, BrainResponse, OmniBrain


def _format_currency(value: Decimal) -> str:
    normalized = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {normalized}"


class BrainWorker(QThread):
    """Background worker that keeps the UI responsive while the brain runs."""

    response_ready = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, brain: OmniBrain, user_input: str, session_id: str) -> None:
        super().__init__()
        self._brain = brain
        self._user_input = user_input
        self._session_id = session_id

    def run(self) -> None:
        try:
            response = self._brain.process_message(self._user_input, self._session_id)
        except BrainError as exc:
            self.error_occurred.emit(str(exc))
            return
        except Exception:
            self.error_occurred.emit("Falha inesperada ao consultar o OmniBrain.")
            return

        self.response_ready.emit(response)


class SalesModuleWidget(QWidget):
    """Operate sales without bypassing the deterministic engine, FSM or brain."""

    sale_completed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._brain = OmniBrain()
        self._brain_worker: BrainWorker | None = None
        self._chat_session_id: str | None = None
        self._fsm: SaleFSM | None = None
        self._selected_product: Product | None = None
        self._proposal_origin = OriginType.HUMANO
        self._suppress_origin_tracking = False
        self._pending_suggested_price: Decimal | None = None
        self._chat_locked = False
        self._thinking_dots = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(450)
        self._thinking_timer.timeout.connect(self._advance_loading_indicator)

        self._build_ui()
        self.refresh_products()
        self._start_new_attendance(announce=False)
        self._sync_controls_for_state()

    def refresh_products(self) -> None:
        """Reload the active product list shown in the operation module."""

        products = list_products(
            only_active=True,
            search_term=self._search_input.text().strip(),
        )
        self._products_table.setRowCount(len(products))

        for row_index, product in enumerate(products):
            values = (
                str(product.id or ""),
                product.sku,
                product.nome,
                product.categoria,
                str(product.estoque_atual),
                _format_currency(product.preco_tabela),
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(256, product.id)
                self._products_table.setItem(row_index, column_index, item)

        self._products_table.resizeRowsToContents()
        self._status_label.setText(f"{len(products)} produto(s) ativo(s) disponivel(is).")
        if self._selected_product is not None:
            self._restore_selection(self._selected_product.id)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(18)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_box = QVBoxLayout()
        title_box.setSpacing(6)

        title = QLabel("Vendas")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "A interface coleta a proposta; a autorizacao final passa pela FSM, pelo Motor de Regras e pelo OmniBrain."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)

        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Buscar produto para a operacao")
        self._search_input.textChanged.connect(self.refresh_products)

        header_layout.addLayout(title_box, stretch=1)
        header_layout.addWidget(self._search_input)
        root_layout.addWidget(header)

        content_layout = QGridLayout()
        content_layout.setHorizontalSpacing(18)
        content_layout.setVerticalSpacing(18)
        content_layout.addWidget(self._build_products_panel(), 0, 0)
        content_layout.addWidget(self._build_operation_panel(), 0, 1)
        content_layout.addWidget(self._build_chat_panel(), 1, 0, 1, 2)
        content_layout.setColumnStretch(0, 3)
        content_layout.setColumnStretch(1, 2)
        root_layout.addLayout(content_layout)

        self._status_label = QLabel("Selecione um produto ou envie uma mensagem para iniciar o atendimento.")
        self._status_label.setObjectName("statusText")
        root_layout.addWidget(self._status_label)

    def _build_products_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Produtos disponiveis")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self._products_table = QTableWidget(0, 6)
        self._products_table.setHorizontalHeaderLabels(
            ("ID", "SKU", "Nome", "Categoria", "Estoque", "Tabela")
        )
        self._products_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._products_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._products_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._products_table.verticalHeader().setVisible(False)
        self._products_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._products_table.itemSelectionChanged.connect(self._on_product_selected)
        layout.addWidget(self._products_table)

        return panel

    def _build_operation_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("Operacao de venda")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        summary = QFormLayout()
        summary.setSpacing(12)

        self._session_label = QLabel("Nenhum atendimento iniciado")
        self._state_label = QLabel(SaleState.PESQUISA.value)
        self._selected_product_label = QLabel("Nenhum produto selecionado")
        self._table_price_label = QLabel("R$ 0,00")
        self._min_price_label = QLabel("R$ 0,00")
        self._suggested_price_label = QLabel("R$ 0,00")

        summary.addRow("Sessao", self._session_label)
        summary.addRow("Estado", self._state_label)
        summary.addRow("Produto", self._selected_product_label)
        summary.addRow("Preco Tabela", self._table_price_label)
        summary.addRow("Piso Deterministico", self._min_price_label)
        summary.addRow("Sugestao da IA", self._suggested_price_label)

        layout.addLayout(summary)

        form = QFormLayout()
        form.setSpacing(12)

        self._quantity_input = QSpinBox()
        self._quantity_input.setRange(1, 1_000_000)
        self._quantity_input.valueChanged.connect(self._on_quantity_changed)

        self._offered_price_input = QDoubleSpinBox()
        self._offered_price_input.setDecimals(2)
        self._offered_price_input.setMaximum(999999999.99)
        self._offered_price_input.setPrefix("R$ ")
        self._offered_price_input.valueChanged.connect(self._on_offered_price_changed)

        self._notes_input = QTextEdit()
        self._notes_input.setFixedHeight(86)

        form.addRow("Quantidade", self._quantity_input)
        form.addRow("Preco ofertado", self._offered_price_input)
        form.addRow("Observacoes", self._notes_input)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self._start_button = QPushButton("Iniciar negociacao")
        self._start_button.clicked.connect(self._start_negotiation)

        self._validate_button = QPushButton("Validar proposta")
        self._validate_button.clicked.connect(self._validate_proposal)

        self._finish_button = QPushButton("Finalizar venda")
        self._finish_button.setObjectName("primaryButton")
        self._finish_button.clicked.connect(self._finalize_sale)

        self._cancel_button = QPushButton("Cancelar")
        self._cancel_button.clicked.connect(self._cancel_session)

        button_row.addWidget(self._start_button)
        button_row.addWidget(self._validate_button)
        button_row.addWidget(self._finish_button)
        button_row.addWidget(self._cancel_button)
        layout.addLayout(button_row)

        return panel

    def _build_chat_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        title = QLabel("Terminal de atendimento")
        title.setObjectName("sectionTitle")

        self._manual_mode_label = QLabel("MODO MANUAL ATIVO")
        self._manual_mode_label.setObjectName("manualModeLabel")
        self._manual_mode_label.hide()

        header_row.addWidget(title)
        header_row.addStretch(1)
        self._new_session_button = QPushButton("Novo atendimento")
        self._new_session_button.clicked.connect(self._start_new_attendance)
        header_row.addWidget(self._new_session_button)
        header_row.addWidget(self._manual_mode_label)
        layout.addLayout(header_row)

        self._thinking_label = QLabel("OmniBrain pensando")
        self._thinking_label.setObjectName("thinkingLabel")
        self._thinking_label.hide()
        layout.addWidget(self._thinking_label)

        self._chat_history = QTextEdit()
        self._chat_history.setObjectName("chatHistory")
        self._chat_history.setReadOnly(True)
        self._chat_history.setMinimumHeight(240)
        layout.addWidget(self._chat_history)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("Digite a mensagem do cliente aqui")
        self._chat_input.returnPressed.connect(self._send_chat_message)

        self._send_button = QPushButton("Enviar para IA")
        self._send_button.clicked.connect(self._send_chat_message)

        input_row.addWidget(self._chat_input, stretch=1)
        input_row.addWidget(self._send_button)
        layout.addLayout(input_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self._quick_accept_button = QPushButton("Aceitar sugestao")
        self._quick_accept_button.setVisible(False)
        self._quick_accept_button.clicked.connect(self._accept_brain_suggestion)

        self._quick_checkout_button = QPushButton("Confirmar venda")
        self._quick_checkout_button.setVisible(False)
        self._quick_checkout_button.clicked.connect(self._trigger_finalize_from_chat)

        action_row.addWidget(self._quick_accept_button)
        action_row.addWidget(self._quick_checkout_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        helper = QLabel(
            "As mensagens da IA sao apenas sugestoes. O Motor de Regras continua sendo a unica autoridade de validacao e persistencia."
        )
        helper.setObjectName("pageSubtitle")
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self._finalize_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._finalize_shortcut.activated.connect(self._finalize_sale)
        self._finalize_numpad_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self)
        self._finalize_numpad_shortcut.activated.connect(self._finalize_sale)

        return panel

    def _on_product_selected(self) -> None:
        items = self._products_table.selectedItems()
        if not items:
            self._selected_product = None
            self._selected_product_label.setText("Nenhum produto selecionado")
            self._refresh_suggestion()
            self._sync_controls_for_state()
            return

        product_id = items[0].data(256)
        product = next(
            (item for item in list_products(only_active=True) if item.id == int(product_id)),
            None,
        )
        self._selected_product = product
        if product is None:
            self._selected_product_label.setText("Nenhum produto selecionado")
            self._refresh_suggestion()
            self._sync_controls_for_state()
            return

        self._selected_product_label.setText(f"{product.nome} ({product.categoria})")
        if self._fsm is None or self._fsm.state in {SaleState.PESQUISA, SaleState.NEGOCIACAO}:
            self._offered_price_input.setValue(0.0)
        self._refresh_suggestion()
        self._sync_controls_for_state()

    def _on_quantity_changed(self) -> None:
        self._proposal_origin = OriginType.HUMANO
        self._refresh_suggestion()
        if self._fsm is not None and self._fsm.state == SaleState.NEGOCIACAO:
            try:
                self._sync_fsm_proposal()
            except (EngineError, FSMError):
                return

    def _on_offered_price_changed(self, _value: float) -> None:
        if self._suppress_origin_tracking:
            return
        self._proposal_origin = OriginType.HUMANO

    def _set_offered_price(
        self,
        value: Decimal,
        *,
        origin: OriginType | None = None,
    ) -> None:
        try:
            self._suppress_origin_tracking = True
            self._offered_price_input.setValue(float(value))
        finally:
            self._suppress_origin_tracking = False

        if origin is not None:
            self._proposal_origin = origin

    def _refresh_suggestion(self) -> None:
        if self._selected_product is None or self._selected_product.id is None:
            self._table_price_label.setText("R$ 0,00")
            self._min_price_label.setText("R$ 0,00")
            self._suggested_price_label.setText("R$ 0,00")
            if self._fsm is None or self._fsm.state != SaleState.CHECKOUT:
                self._set_offered_price(Decimal("0.00"), origin=OriginType.HUMANO)
            return

        try:
            suggestion = build_pricing_suggestion(
                self._selected_product.id,
                self._quantity_input.value(),
            )
        except EngineError as exc:
            self._status_label.setText(str(exc))
            return

        self._table_price_label.setText(_format_currency(suggestion.produto.preco_tabela))
        self._min_price_label.setText(_format_currency(suggestion.preco_minimo_unitario))
        self._suggested_price_label.setText(_format_currency(suggestion.preco_sugerido_unitario))
        if self._offered_price_input.value() == 0.0 and self._is_price_editable():
            self._set_offered_price(
                suggestion.preco_sugerido_unitario,
                origin=OriginType.SISTEMA,
            )

    def _current_proposal(self) -> SaleProposal:
        if self._selected_product is None or self._selected_product.id is None:
            raise ProductNotFoundError("Selecione um produto ativo para continuar.")

        return SaleProposal(
            produto_id=self._selected_product.id,
            quantidade=self._quantity_input.value(),
            preco_ofertado_unitario=Decimal(str(self._offered_price_input.value())),
            origem=self._proposal_origin,
            negociacao_status=self._fsm_state_to_status(),
            observacoes=self._notes_input.toPlainText().strip(),
        )

    def _fsm_state_to_status(self) -> NegotiationStatus:
        if self._fsm is None:
            return NegotiationStatus.PESQUISA

        mapping = {
            SaleState.PESQUISA: NegotiationStatus.PESQUISA,
            SaleState.NEGOCIACAO: NegotiationStatus.NEGOCIANDO,
            SaleState.CHECKOUT: NegotiationStatus.CHECKOUT,
            SaleState.CONCLUIDA: NegotiationStatus.CONCLUIDA,
            SaleState.CANCELADA: NegotiationStatus.CANCELADA,
        }
        return mapping[self._fsm.state]

    def _active_session_id(self) -> str:
        session_id = self._session_reference()
        if session_id is not None:
            return session_id
        self._start_new_attendance(announce=False)
        return self._session_reference() or str(uuid4())

    def _session_reference(self) -> str | None:
        if self._fsm is not None:
            return self._fsm.session_id
        return self._chat_session_id

    def _start_negotiation(self) -> None:
        try:
            self._ensure_negotiation_session(
                force_new=self._fsm is None or self._fsm.state in {SaleState.CONCLUIDA, SaleState.CANCELADA}
            )
        except (EngineError, FSMError) as exc:
            QMessageBox.warning(self, "Negociacao", str(exc))
            return

        self._status_label.setText("Negociacao iniciada. Use o chat ou valide a proposta para liberar checkout.")
        self._sync_controls_for_state()

    def _start_new_attendance(self, checked: bool = False, *, announce: bool = True) -> None:
        del checked
        if self._brain_worker is not None and self._brain_worker.isRunning():
            QMessageBox.information(
                self,
                "Novo atendimento",
                "Aguarde o OmniBrain terminar o processamento atual antes de iniciar outra sessao.",
            )
            return

        previous_session_id = self._session_reference()
        if previous_session_id is not None:
            self._brain.discard_session(previous_session_id)
            SaleFSM.discard_session(previous_session_id)

        self._thinking_timer.stop()
        self._thinking_label.hide()
        self._manual_mode_label.hide()
        self._chat_history.clear()
        self._chat_input.clear()
        self._chat_locked = False
        self._clear_pending_suggestion()
        self._clear_quick_checkout_action()
        self._proposal_origin = OriginType.HUMANO
        self._notes_input.clear()
        self._quantity_input.setValue(1)
        self._set_offered_price(Decimal("0.00"), origin=OriginType.HUMANO)
        self._selected_product = None
        self._products_table.clearSelection()
        self._selected_product_label.setText("Nenhum produto selecionado")
        self._table_price_label.setText("R$ 0,00")
        self._min_price_label.setText("R$ 0,00")
        self._suggested_price_label.setText("R$ 0,00")
        self._chat_input.setPlaceholderText("Digite a mensagem do cliente aqui")

        self._chat_session_id = str(uuid4())
        self._fsm = SaleFSM(self._chat_session_id)

        if announce:
            self._append_chat_message(
                speaker="Sistema",
                text=f"Novo atendimento iniciado. Sessao {self._chat_session_id} pronta para pesquisa.",
                color="#0f5132",
                background="#e7f6ec",
                alignment="left",
            )

        self._status_label.setText("Novo atendimento iniciado. Aguardando a pesquisa do cliente.")
        self._sync_session_labels()
        self._apply_chat_entry_state()
        self._sync_controls_for_state()

    def _ensure_negotiation_session(
        self,
        *,
        force_new: bool = False,
        preferred_session_id: str | None = None,
    ) -> None:
        if self._selected_product is None or self._selected_product.id is None:
            raise ProductNotFoundError("Selecione um produto ativo antes de iniciar a negociacao.")

        if (
            self._fsm is not None
            and self._fsm.state in {SaleState.NEGOCIACAO, SaleState.CHECKOUT}
            and not force_new
        ):
            self._sync_session_labels()
            return

        if self._fsm is not None and self._fsm.state == SaleState.PESQUISA and not force_new:
            snapshot = self._fsm.start_negotiation(self._proposal_for_state(SaleState.PESQUISA))
            self._session_label.setText(snapshot.session_id)
            self._state_label.setText(snapshot.state.value)
            self._sync_controls_for_state()
            return

        previous_session_id = self._session_reference()
        session_id = preferred_session_id or str(uuid4())
        if previous_session_id is not None and previous_session_id != session_id:
            self._brain.discard_session(previous_session_id)
            SaleFSM.discard_session(previous_session_id)

        self._chat_session_id = session_id
        self._fsm = SaleFSM(session_id)
        snapshot = self._fsm.start_negotiation(self._proposal_for_state(SaleState.PESQUISA))
        self._session_label.setText(snapshot.session_id)
        self._state_label.setText(snapshot.state.value)
        self._sync_controls_for_state()

    def _proposal_for_state(self, state: SaleState) -> SaleProposal:
        proposal = self._current_proposal()
        status_mapping = {
            SaleState.PESQUISA: NegotiationStatus.PESQUISA,
            SaleState.NEGOCIACAO: NegotiationStatus.NEGOCIANDO,
            SaleState.CHECKOUT: NegotiationStatus.CHECKOUT,
            SaleState.CONCLUIDA: NegotiationStatus.CONCLUIDA,
            SaleState.CANCELADA: NegotiationStatus.CANCELADA,
        }
        return SaleProposal(
            produto_id=proposal.produto_id,
            quantidade=proposal.quantidade,
            preco_ofertado_unitario=proposal.preco_ofertado_unitario,
            origem=proposal.origem,
            negociacao_status=status_mapping[state],
            observacoes=proposal.observacoes,
        )

    def _sync_fsm_proposal(self) -> None:
        if self._fsm is None:
            raise MissingProposalError("Nao ha sessao ativa para sincronizar a proposta.")
        if self._fsm.state not in {SaleState.NEGOCIACAO, SaleState.CHECKOUT}:
            return
        self._fsm.update_proposal(self._proposal_for_state(self._fsm.state))
        self._sync_session_labels()

    def _validate_proposal(self) -> None:
        if self._fsm is None:
            QMessageBox.information(self, "Vendas", "Inicie a negociacao antes de validar.")
            return

        try:
            if self._fsm.state == SaleState.NEGOCIACAO:
                self._sync_fsm_proposal()
                validation = self._fsm.authorize_checkout()
            elif self._fsm.state == SaleState.CHECKOUT:
                validation = self._fsm.snapshot().last_validation
                if validation is None:
                    raise MissingProposalError("Nao existe validacao armazenada para o checkout.")
            else:
                raise FSMError("O atendimento precisa estar em negociacao para validar a proposta.")
        except (EngineError, FSMError, MissingProposalError) as exc:
            QMessageBox.warning(self, "Validacao", str(exc))
            return

        self._set_offered_price(validation.proposta.preco_ofertado_unitario)
        self._clear_pending_suggestion()
        self._status_label.setText(
            f"Checkout liberado. Piso: {_format_currency(validation.preco_minimo_unitario)}."
        )
        self._sync_session_labels()
        self._sync_controls_for_state()

    def _finalize_sale(self) -> None:
        if self._fsm is None:
            QMessageBox.information(self, "Vendas", "Inicie e valide a negociacao antes de finalizar.")
            return

        try:
            result = self._fsm.finalize_sale()
        except (EngineError, FSMError, MissingProposalError) as exc:
            QMessageBox.warning(self, "Venda", str(exc))
            return

        self._status_label.setText(
            f"Venda concluida. ID {result.venda.id} | Estoque restante: {result.estoque_restante}"
        )
        QMessageBox.information(
            self,
            "Venda concluida",
            f"Venda {result.venda.id} concluida com total de {_format_currency(result.venda.total_liquido)}.",
        )
        self._append_chat_message(
            speaker="Sistema",
            text=(
                f"\u2705 Venda #{result.venda.id} finalizada com sucesso!\n"
                f"Total: {_format_currency(result.venda.total_liquido)}.\n"
                "Clique em 'Novo atendimento' para iniciar a proxima conversa."
            ),
            color="#0f5132",
            background="#e7f6ec",
            alignment="left",
        )
        self.sale_completed.emit()
        self.refresh_products()
        self._clear_pending_suggestion()
        self._clear_quick_checkout_action()
        self._chat_locked = True
        self._manual_mode_label.hide()
        self._chat_input.clear()
        self._chat_input.setPlaceholderText("Clique em 'Novo atendimento' para iniciar outra venda.")
        self._sync_session_labels()
        self._apply_chat_entry_state()
        self._sync_controls_for_state()

    def _cancel_session(self) -> None:
        if self._fsm is None:
            QMessageBox.information(self, "Vendas", "Nenhum atendimento ativo para cancelar.")
            return

        reason, accepted = QInputDialog.getText(
            self,
            "Cancelar atendimento",
            "Informe o motivo do cancelamento:",
        )
        if not accepted:
            return

        try:
            snapshot = self._fsm.cancel(reason)
        except FSMError as exc:
            QMessageBox.warning(self, "Cancelamento", str(exc))
            return

        self._append_chat_message(
            speaker="Sistema",
            text=f"Atendimento cancelado: {snapshot.cancellation_reason}",
            color="#b42318",
            background="#fde7e9",
            alignment="left",
        )
        self._status_label.setText(f"Atendimento cancelado: {snapshot.cancellation_reason}")
        self._clear_pending_suggestion()
        self._clear_quick_checkout_action()
        self._sync_session_labels()
        self._sync_controls_for_state()

    def _send_chat_message(self) -> None:
        if self._chat_locked:
            self._status_label.setText(
                "Atendimento encerrado. Clique em 'Novo atendimento' para iniciar outra sessao."
            )
            return

        message = self._chat_input.text().strip()
        if not message:
            return

        session_id = self._active_session_id()
        self._append_chat_message(
            speaker="Cliente",
            text=message,
            color="#1f2933",
            background="#e5e7eb",
            alignment="right",
        )
        self._chat_input.clear()
        self._manual_mode_label.hide()
        self._set_chat_busy(True)

        self._brain_worker = BrainWorker(self._brain, message, session_id)
        self._brain_worker.response_ready.connect(self._handle_brain_response)
        self._brain_worker.error_occurred.connect(self._handle_brain_error)
        self._brain_worker.finished.connect(self._on_brain_worker_finished)
        self._brain_worker.start()

    def _handle_brain_response(self, response: BrainResponse) -> None:
        active_session_id = self._session_reference()
        if active_session_id is not None and response.session_id != active_session_id:
            return

        self._append_chat_message(
            speaker="OmniBrain",
            text=response.reply_text,
            color="#1f4e79",
            background="#e8f1ff",
            alignment="left",
        )
        self._manual_mode_label.setVisible(response.safe_mode)

        if response.product_id is not None:
            self._select_product_by_id(response.product_id)

        if response.proposed_price is not None and self._is_price_editable():
            self._set_offered_price(response.proposed_price, origin=OriginType.IA)
            self._show_pending_suggestion(response.proposed_price)
        else:
            self._clear_pending_suggestion()

        if response.intent != BrainIntent.CHECKOUT or response.safe_mode:
            self._clear_quick_checkout_action()

        if response.validation_note:
            self._status_label.setText(response.validation_note)
        else:
            self._status_label.setText(response.reply_text)

        if (
            response.product_id is not None
            and response.proposed_price is not None
            and response.intent in {BrainIntent.NEGOCIACAO, BrainIntent.CHECKOUT}
            and not response.safe_mode
        ):
            try:
                self._ensure_negotiation_session(
                    force_new=self._fsm is None or self._fsm.state in {SaleState.CONCLUIDA, SaleState.CANCELADA},
                    preferred_session_id=response.session_id,
                )
                self._sync_fsm_proposal()
            except (EngineError, FSMError) as exc:
                QMessageBox.warning(self, "OmniBrain", str(exc))

        if response.intent == BrainIntent.CHECKOUT and not response.safe_mode:
            try:
                self._validate_proposal()
            except Exception:
                # _validate_proposal already communicates failures to the user.
                pass
            if self._fsm is not None and self._fsm.state == SaleState.CHECKOUT:
                self._show_quick_checkout_action()

        self._sync_session_labels()
        self._sync_controls_for_state()

    def _handle_brain_error(self, message: str) -> None:
        self._manual_mode_label.show()
        self._append_chat_message(
            speaker="Sistema",
            text=message,
            color="#b42318",
            background="#fde7e9",
            alignment="left",
        )
        self._clear_pending_suggestion()
        self._status_label.setText(message)

    def _on_brain_worker_finished(self) -> None:
        self._set_chat_busy(False)
        self._brain_worker = None

    def _append_chat_message(
        self,
        *,
        speaker: str,
        text: str,
        color: str,
        background: str,
        alignment: str,
    ) -> None:
        align = "right" if alignment == "right" else "left"
        html = (
            f"<div style='margin: 0 0 10px 0; text-align: {align};'>"
            f"<div style='display: inline-block; max-width: 88%; "
            f"background: {background}; border-radius: 14px; padding: 10px 12px;'>"
            f"<div style='color:{color}; font-weight:700; margin-bottom: 4px;'>{escape(speaker)}</div>"
            f"<div style='color:{color}; white-space: pre-wrap;'>{escape(text)}</div>"
            f"</div>"
            f"</div>"
        )
        self._chat_history.insertHtml(html)
        self._chat_history.append("")

    def _set_chat_busy(self, busy: bool) -> None:
        self._new_session_button.setDisabled(busy)
        if busy:
            self._thinking_dots = 0
            self._thinking_label.setText("OmniBrain pensando")
            self._thinking_label.show()
            self._thinking_timer.start()
            self._status_label.setText("Consultando o OmniBrain...")
            self._apply_chat_entry_state(is_busy=busy)
            return

        self._thinking_timer.stop()
        self._thinking_label.hide()
        self._apply_chat_entry_state(is_busy=busy)

    def _advance_loading_indicator(self) -> None:
        self._thinking_dots = (self._thinking_dots + 1) % 4
        suffix = "." * self._thinking_dots
        self._thinking_label.setText(f"OmniBrain pensando{suffix}")

    def _apply_chat_entry_state(self, *, is_busy: bool | None = None) -> None:
        busy = is_busy
        if busy is None:
            busy = self._brain_worker is not None and self._brain_worker.isRunning()
        enabled = not busy and not self._chat_locked
        self._chat_input.setEnabled(enabled)
        self._send_button.setEnabled(enabled)
        self._new_session_button.setEnabled(not busy)

    def _show_pending_suggestion(self, suggested_price: Decimal) -> None:
        self._pending_suggested_price = suggested_price
        self._quick_accept_button.setText(
            f"Aceitar Sugestao ({_format_currency(suggested_price)})"
        )
        self._quick_accept_button.setVisible(True)
        self._quick_accept_button.setEnabled(True)

    def _clear_pending_suggestion(self) -> None:
        self._pending_suggested_price = None
        self._quick_accept_button.setVisible(False)
        self._quick_accept_button.setEnabled(False)

    def _show_quick_checkout_action(self) -> None:
        self._quick_checkout_button.setVisible(True)
        self._quick_checkout_button.setEnabled(
            not self._chat_locked and self._fsm is not None and self._fsm.state == SaleState.CHECKOUT
        )

    def _clear_quick_checkout_action(self) -> None:
        self._quick_checkout_button.setVisible(False)
        self._quick_checkout_button.setEnabled(False)

    def _trigger_finalize_from_chat(self) -> None:
        if self._quick_checkout_button.isVisible() and self._quick_checkout_button.isEnabled():
            self._finish_button.click()

    def _accept_brain_suggestion(self) -> None:
        if self._pending_suggested_price is None:
            return

        if self._selected_product is None or self._selected_product.id is None:
            QMessageBox.information(self, "Sugestao", "Selecione um produto antes de aceitar a sugestao.")
            return

        try:
            self._ensure_negotiation_session(
                force_new=self._fsm is None or self._fsm.state in {SaleState.CONCLUIDA, SaleState.CANCELADA},
                preferred_session_id=self._active_session_id(),
            )
            self._set_offered_price(self._pending_suggested_price, origin=OriginType.IA)
            self._sync_fsm_proposal()
            self._validate_proposal()
            self._append_chat_message(
                speaker="Sistema",
                text=f"Sugestao confirmada para validacao em {_format_currency(self._pending_suggested_price)}.",
                color="#0f5132",
                background="#e7f6ec",
                alignment="left",
            )
        except (EngineError, FSMError, MissingProposalError) as exc:
            QMessageBox.warning(self, "Sugestao", str(exc))
            return

        self._clear_pending_suggestion()

    def _select_product_by_id(self, product_id: int) -> None:
        for row_index in range(self._products_table.rowCount()):
            item = self._products_table.item(row_index, 0)
            if item is not None and int(item.data(256)) == product_id:
                self._products_table.selectRow(row_index)
                return

        refreshed = next(
            (product for product in list_products(only_active=True) if product.id == product_id),
            None,
        )
        if refreshed is not None:
            if self._search_input.text().strip():
                self._search_input.clear()
            self._selected_product = refreshed
            self.refresh_products()
            self._restore_selection(product_id)

    def _restore_selection(self, product_id: int | None) -> None:
        if product_id is None:
            return

        for row_index in range(self._products_table.rowCount()):
            item = self._products_table.item(row_index, 0)
            if item is not None and int(item.data(256)) == product_id:
                self._products_table.selectRow(row_index)
                break

    def _sync_session_labels(self) -> None:
        if self._fsm is None:
            self._session_label.setText(self._chat_session_id or "Nenhum atendimento iniciado")
            self._state_label.setText(SaleState.PESQUISA.value)
            return

        self._session_label.setText(self._fsm.session_id)
        self._state_label.setText(self._fsm.state.value)

    def _sync_controls_for_state(self) -> None:
        has_product = self._selected_product is not None and self._selected_product.id is not None
        state = self._fsm.state if self._fsm is not None else SaleState.PESQUISA

        self._start_button.setEnabled(has_product and state in {SaleState.PESQUISA, SaleState.CONCLUIDA, SaleState.CANCELADA})
        if self._chat_locked:
            self._start_button.setEnabled(False)
        self._validate_button.setEnabled(self._fsm is not None and state == SaleState.NEGOCIACAO)
        self._finish_button.setEnabled(self._fsm is not None and state == SaleState.CHECKOUT)
        self._cancel_button.setEnabled(self._fsm is not None and state in {SaleState.NEGOCIACAO, SaleState.CHECKOUT})

        editable = self._is_price_editable()
        self._quantity_input.setEnabled(has_product and editable)
        self._offered_price_input.setEnabled(has_product and editable)
        self._notes_input.setReadOnly(not editable)
        self._quick_accept_button.setEnabled(
            self._pending_suggested_price is not None and editable and not self._chat_locked
        )
        self._quick_checkout_button.setEnabled(
            self._quick_checkout_button.isVisible()
            and self._fsm is not None
            and state == SaleState.CHECKOUT
            and not self._chat_locked
        )
        self._apply_chat_entry_state()

    def _is_price_editable(self) -> bool:
        return self._fsm is None or self._fsm.state not in {
            SaleState.CHECKOUT,
            SaleState.CONCLUIDA,
            SaleState.CANCELADA,
        }
