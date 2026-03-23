"""Finite State Machine for Omni-Core sales sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import Lock
from typing import ClassVar, Final

from src.core.engine import (
    ProductNotFoundError,
    SaleExecutionResult,
    ValidationResult,
    evaluate_proposal,
    get_product,
    process_sale,
)
from src.database import AuditRepository, transaction
from src.domain.models import (
    AuditDecision,
    AuditEntity,
    AuditEvent,
    AuditLog,
    NegotiationStatus,
    OriginType,
    SaleProposal,
)


class SaleState(str, Enum):
    """Public FSM states for the sale lifecycle."""

    PESQUISA = "PESQUISA"
    NEGOCIACAO = "NEGOCIACAO"
    CHECKOUT = "CHECKOUT"
    CONCLUIDA = "CONCLUIDA"
    CANCELADA = "CANCELADA"


class FSMError(Exception):
    """Base exception for the sale FSM."""


class InvalidStateTransitionError(FSMError):
    """Raised when a forbidden transition is attempted."""


class MissingProposalError(FSMError):
    """Raised when an operation depends on a proposal that does not exist."""


@dataclass(frozen=True, slots=True)
class SaleSessionSnapshot:
    """Serializable view of the in-memory sale session state."""

    session_id: str
    state: SaleState
    selected_product_id: int | None = None
    last_proposal: SaleProposal | None = None
    last_validation: ValidationResult | None = None
    last_sale: SaleExecutionResult | None = None
    cancellation_reason: str | None = None


_STATE_TO_NEGOTIATION_STATUS: Final[dict[SaleState, NegotiationStatus]] = {
    SaleState.PESQUISA: NegotiationStatus.PESQUISA,
    SaleState.NEGOCIACAO: NegotiationStatus.NEGOCIANDO,
    SaleState.CHECKOUT: NegotiationStatus.CHECKOUT,
    SaleState.CONCLUIDA: NegotiationStatus.CONCLUIDA,
    SaleState.CANCELADA: NegotiationStatus.CANCELADA,
}

_ALLOWED_TRANSITIONS: Final[dict[SaleState, set[SaleState]]] = {
    SaleState.PESQUISA: {SaleState.NEGOCIACAO, SaleState.CANCELADA},
    SaleState.NEGOCIACAO: {SaleState.CHECKOUT, SaleState.CANCELADA},
    SaleState.CHECKOUT: {SaleState.CONCLUIDA, SaleState.CANCELADA},
    SaleState.CONCLUIDA: {SaleState.CANCELADA},
    SaleState.CANCELADA: set(),
}


def _proposal_for_state(proposal: SaleProposal, state: SaleState) -> SaleProposal:
    return replace(
        proposal,
        negociacao_status=_STATE_TO_NEGOTIATION_STATUS[state],
    )


class SaleFSM:
    """Deterministic FSM that orchestrates the sale lifecycle.

    The FSM is scoped by ``session_id`` so multiple atendimento sessions can be
    managed independently in memory.
    """

    _registry: ClassVar[dict[str, SaleSessionSnapshot]] = {}
    _registry_lock: ClassVar[Lock] = Lock()

    def __init__(self, session_id: str) -> None:
        if not session_id.strip():
            raise FSMError("session_id e obrigatorio para iniciar a FSM.")

        self._snapshot = SaleSessionSnapshot(
            session_id=session_id.strip(),
            state=SaleState.PESQUISA,
        )
        self._store_snapshot()

    @classmethod
    def get_snapshot(cls, session_id: str) -> SaleSessionSnapshot | None:
        """Return the latest known snapshot for a session."""

        with cls._registry_lock:
            return cls._registry.get(session_id)

    @classmethod
    def discard_session(cls, session_id: str) -> None:
        """Discard the in-memory snapshot for a finished or replaced session."""

        if not session_id.strip():
            return
        with cls._registry_lock:
            cls._registry.pop(session_id.strip(), None)

    @property
    def session_id(self) -> str:
        """Return the logical session identifier."""

        return self._snapshot.session_id

    @property
    def state(self) -> SaleState:
        """Return the current FSM state."""

        return self._snapshot.state

    def snapshot(self) -> SaleSessionSnapshot:
        """Return a copy-safe snapshot of the current session."""

        return self._snapshot

    def start_negotiation(self, proposal: SaleProposal) -> SaleSessionSnapshot:
        """Transition from ``PESQUISA`` to ``NEGOCIACAO``.

        Raises:
            InvalidStateTransitionError: If the current state is not ``PESQUISA``.
            ProductNotFoundError: If the selected product does not exist or is inactive.
        """

        self._assert_transition_allowed(SaleState.NEGOCIACAO)
        product = get_product(proposal.produto_id)
        if not product.ativo:
            raise ProductNotFoundError(
                f"Produto inativo nao pode iniciar negociacao: {proposal.produto_id}."
            )

        negotiation_proposal = _proposal_for_state(proposal, SaleState.NEGOCIACAO)
        previous = self._snapshot
        self._snapshot = replace(
            self._snapshot,
            state=SaleState.NEGOCIACAO,
            selected_product_id=proposal.produto_id,
            last_proposal=negotiation_proposal,
            last_validation=None,
            cancellation_reason=None,
        )
        self._record_transition(
            previous_state=previous.state,
            target_state=self._snapshot.state,
            origin=negotiation_proposal.origem,
            reason="Produto selecionado e proposta iniciada para negociacao.",
            context={
                "session_id": self.session_id,
                "produto_id": proposal.produto_id,
                "produto_nome": product.nome,
                "quantidade": negotiation_proposal.quantidade,
            },
        )
        self._store_snapshot()
        return self._snapshot

    def authorize_checkout(self) -> ValidationResult:
        """Validate the current proposal and transition to ``CHECKOUT``.

        Raises:
            InvalidStateTransitionError: If the current state is not ``NEGOCIACAO``.
            MissingProposalError: If there is no proposal active in the session.
            EngineError: If the deterministic engine rejects the proposal.
        """

        self._assert_transition_allowed(SaleState.CHECKOUT)
        if self._snapshot.last_proposal is None:
            raise MissingProposalError("Nao existe proposta ativa para autorizar o checkout.")

        validation = evaluate_proposal(self._snapshot.last_proposal)
        checkout_proposal = _proposal_for_state(validation.proposta, SaleState.CHECKOUT)

        previous = self._snapshot
        self._snapshot = replace(
            self._snapshot,
            state=SaleState.CHECKOUT,
            last_proposal=checkout_proposal,
            last_validation=validation,
            cancellation_reason=None,
        )
        self._record_transition(
            previous_state=previous.state,
            target_state=self._snapshot.state,
            origin=validation.proposta.origem,
            reason="Motor de Regras validou a proposta e liberou checkout.",
            context={
                "session_id": self.session_id,
                "produto_id": validation.produto.id,
                "preco_minimo_unitario": str(validation.preco_minimo_unitario),
                "preco_ofertado_unitario": str(validation.proposta.preco_ofertado_unitario),
                "total_liquido": str(validation.total_liquido),
            },
        )
        self._store_snapshot()
        return validation

    def update_proposal(self, proposal: SaleProposal) -> SaleSessionSnapshot:
        """Replace the current proposal while the session is still negotiable.

        Raises:
            InvalidStateTransitionError: If the current state is not ``NEGOCIACAO`` or ``CHECKOUT``.
            ProductNotFoundError: If the selected product does not exist or is inactive.
        """

        if self._snapshot.state not in {SaleState.NEGOCIACAO, SaleState.CHECKOUT}:
            raise InvalidStateTransitionError(
                "A proposta so pode ser atualizada durante negociacao ou checkout."
            )

        product = get_product(proposal.produto_id)
        if not product.ativo:
            raise ProductNotFoundError(
                f"Produto inativo nao pode receber nova proposta: {proposal.produto_id}."
            )

        updated_proposal = _proposal_for_state(proposal, self._snapshot.state)
        self._snapshot = replace(
            self._snapshot,
            selected_product_id=proposal.produto_id,
            last_proposal=updated_proposal,
        )
        self._record_transition(
            previous_state=self._snapshot.state,
            target_state=self._snapshot.state,
            origin=updated_proposal.origem,
            reason="Proposta atualizada dentro da sessao ativa.",
            event=AuditEvent.PROPOSTA_ATUALIZADA,
            context={
                "session_id": self.session_id,
                "produto_id": proposal.produto_id,
                "quantidade": updated_proposal.quantidade,
                "preco_ofertado_unitario": str(updated_proposal.preco_ofertado_unitario),
            },
        )
        self._store_snapshot()
        return self._snapshot

    def finalize_sale(self) -> SaleExecutionResult:
        """Persist the sale and transition from ``CHECKOUT`` to ``CONCLUIDA``.

        Raises:
            InvalidStateTransitionError: If the current state is not ``CHECKOUT``.
            MissingProposalError: If there is no validated proposal ready to persist.
            EngineError: If persistence or stock deduction fails.
        """

        self._assert_transition_allowed(SaleState.CONCLUIDA)
        if self._snapshot.last_proposal is None:
            raise MissingProposalError("Nao existe proposta pronta para finalizar a venda.")

        result = process_sale(self._snapshot.last_proposal)
        previous = self._snapshot
        self._snapshot = replace(
            self._snapshot,
            state=SaleState.CONCLUIDA,
            last_sale=result,
            cancellation_reason=None,
        )
        self._record_transition(
            previous_state=previous.state,
            target_state=self._snapshot.state,
            origin=result.venda.origem,
            reason="Venda persistida com sucesso e estoque baixado no SQLite.",
            context={
                "session_id": self.session_id,
                "venda_id": result.venda.id,
                "produto_id": result.venda.produto_id,
                "estoque_restante": result.estoque_restante,
                "total_liquido": str(result.venda.total_liquido),
            },
        )
        self._store_snapshot()
        return result

    def cancel(
        self,
        reason: str,
        *,
        origin: OriginType = OriginType.HUMANO,
    ) -> SaleSessionSnapshot:
        """Cancel the current session from any non-cancelled state.

        Raises:
            InvalidStateTransitionError: If the session is already cancelled.
            FSMError: If the cancellation reason is empty.
        """

        if not reason.strip():
            raise FSMError("Informe o motivo do cancelamento do atendimento.")
        if self._snapshot.state == SaleState.CANCELADA:
            raise InvalidStateTransitionError("O atendimento ja esta cancelado.")
        self._assert_transition_allowed(SaleState.CANCELADA)

        previous = self._snapshot
        self._snapshot = replace(
            self._snapshot,
            state=SaleState.CANCELADA,
            cancellation_reason=reason.strip(),
        )
        self._record_transition(
            previous_state=previous.state,
            target_state=self._snapshot.state,
            origin=origin,
            reason=reason.strip(),
            event=AuditEvent.ATENDIMENTO_CANCELADO,
            context={
                "session_id": self.session_id,
                "produto_id": self._snapshot.selected_product_id,
                "ultima_proposta": {
                    "quantidade": self._snapshot.last_proposal.quantidade,
                    "preco_ofertado_unitario": str(
                        self._snapshot.last_proposal.preco_ofertado_unitario
                    ),
                }
                if self._snapshot.last_proposal
                else None,
            },
        )
        self._store_snapshot()
        return self._snapshot

    def _assert_transition_allowed(self, target_state: SaleState) -> None:
        allowed = _ALLOWED_TRANSITIONS[self._snapshot.state]
        if target_state not in allowed:
            allowed_labels = ", ".join(state.value for state in sorted(allowed, key=lambda item: item.value))
            raise InvalidStateTransitionError(
                "Transicao de estado proibida. "
                f"Atual: {self._snapshot.state.value}. Destino: {target_state.value}. "
                f"Permitidos: {allowed_labels or 'nenhum'}."
            )

    def _record_transition(
        self,
        *,
        previous_state: SaleState,
        target_state: SaleState,
        origin: OriginType,
        reason: str,
        context: dict[str, object],
        event: AuditEvent = AuditEvent.FSM_TRANSICAO,
    ) -> None:
        entity_id = None
        if self._snapshot.last_sale is not None and self._snapshot.last_sale.venda.id is not None:
            entity_id = self._snapshot.last_sale.venda.id
        elif self._snapshot.selected_product_id is not None:
            entity_id = self._snapshot.selected_product_id

        with transaction() as connection:
            AuditRepository(connection).create(
                AuditLog(
                    entidade=AuditEntity.NEGOCIACAO,
                    entidade_id=entity_id,
                    evento=event,
                    origem=origin,
                    status_negociacao=_STATE_TO_NEGOTIATION_STATUS[target_state],
                    decisao=AuditDecision.REGISTRADA,
                    motivo=reason,
                    contexto={
                        "session_id": self.session_id,
                        "de": previous_state.value,
                        "para": target_state.value,
                        **context,
                    },
                )
            )

    def _store_snapshot(self) -> None:
        with self._registry_lock:
            self._registry[self.session_id] = self._snapshot
