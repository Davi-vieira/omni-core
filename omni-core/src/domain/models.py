"""Typed domain entities and enums for Omni-Core ERP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


class OriginType(str, Enum):
    """Identifies who initiated an action."""

    IA = "IA"
    HUMANO = "HUMANO"
    SISTEMA = "SISTEMA"


class NegotiationStatus(str, Enum):
    """State machine states for the commercial flow."""

    AGUARDANDO = "AGUARDANDO"
    PESQUISA = "PESQUISA"
    NEGOCIANDO = "NEGOCIANDO"
    CHECKOUT = "CHECKOUT"
    CONCLUIDA = "CONCLUIDA"
    CANCELADA = "CANCELADA"


class SaleStatus(str, Enum):
    """Persistence status for a sale."""

    PENDENTE = "PENDENTE"
    CONCLUIDA = "CONCLUIDA"
    CANCELADA = "CANCELADA"
    ESTORNADA = "ESTORNADA"


class AuditEntity(str, Enum):
    """Entity types covered by the audit trail."""

    PRODUTO = "PRODUTO"
    VENDA = "VENDA"
    NEGOCIACAO = "NEGOCIACAO"
    SISTEMA = "SISTEMA"


class AuditDecision(str, Enum):
    """Decision taken by the deterministic rule engine."""

    APROVADA = "APROVADA"
    REJEITADA = "REJEITADA"
    REGISTRADA = "REGISTRADA"


class AuditEvent(str, Enum):
    """Audit events emitted by the rule engine and persistence layer."""

    PRODUTO_REGISTRADO = "PRODUTO_REGISTRADO"
    PRODUTO_ATUALIZADO = "PRODUTO_ATUALIZADO"
    PRODUTO_DESATIVADO = "PRODUTO_DESATIVADO"
    PROPOSTA_ATUALIZADA = "PROPOSTA_ATUALIZADA"
    PROPOSTA_VALIDADA = "PROPOSTA_VALIDADA"
    PROPOSTA_REJEITADA = "PROPOSTA_REJEITADA"
    FSM_TRANSICAO = "FSM_TRANSICAO"
    ATENDIMENTO_CANCELADO = "ATENDIMENTO_CANCELADO"
    VENDA_CONCLUIDA = "VENDA_CONCLUIDA"
    VENDA_REJEITADA = "VENDA_REJEITADA"


@dataclass(frozen=True, slots=True)
class Product:
    """Inventory entity used by the ERP core."""

    sku: str
    nome: str
    categoria: str
    descricao: str
    preco_custo: Decimal
    preco_tabela: Decimal
    margem_ia: Decimal
    margem_minima: Decimal
    estoque_atual: int
    ativo: bool = True
    id: int | None = None
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class Sale:
    """Persisted sale entity."""

    produto_id: int
    sku: str
    produto_nome: str
    quantidade: int
    preco_tabela_unitario: Decimal
    preco_custo_unitario: Decimal
    margem_ia: Decimal
    margem_minima: Decimal
    preco_minimo_unitario: Decimal
    preco_ofertado_unitario: Decimal
    total_bruto: Decimal
    total_liquido: Decimal
    origem: OriginType
    status: SaleStatus
    negociacao_status: NegotiationStatus
    observacoes: str = ""
    id: int | None = None
    criado_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditLog:
    """Immutable audit register of the deterministic engine."""

    entidade: AuditEntity
    evento: AuditEvent
    origem: OriginType
    decisao: AuditDecision
    contexto: Mapping[str, Any] = field(default_factory=dict)
    entidade_id: int | None = None
    status_negociacao: NegotiationStatus | None = None
    motivo: str = ""
    id: int | None = None
    criado_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class SaleProposal:
    """Commercial proposal received from the suggestion layer."""

    produto_id: int
    quantidade: int
    preco_ofertado_unitario: Decimal
    origem: OriginType
    negociacao_status: NegotiationStatus
    observacoes: str = ""
