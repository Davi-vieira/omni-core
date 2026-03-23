"""Deterministic business rules for Omni-Core ERP."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from src.core.audit_logger import BusinessAuditLogger
from src.database import AuditRepository, ProductRepository, SaleRepository, get_connection, transaction
from src.domain.models import (
    AuditDecision,
    AuditEntity,
    AuditEvent,
    AuditLog,
    NegotiationStatus,
    OriginType,
    Product,
    Sale,
    SaleProposal,
    SaleStatus,
)

MONEY_STEP = Decimal("0.01")
RATIO_STEP = Decimal("0.0001")
ZERO = Decimal("0.00")
ONE = Decimal("1.00")


class EngineError(Exception):
    """Base exception for business-rule errors."""


class ProductNotFoundError(EngineError):
    """Raised when a product is not found or is inactive."""


class InvalidProductError(EngineError):
    """Raised when a product payload is invalid."""


class InvalidQuantityError(EngineError):
    """Raised when the requested quantity is invalid."""


class InsufficientStockError(EngineError):
    """Raised when the requested quantity is not available."""


class PriceValidationError(EngineError):
    """Raised when an offer violates the minimum price rule."""


class NegotiationStatusError(EngineError):
    """Raised when the FSM state forbids the requested action."""


@dataclass(frozen=True, slots=True)
class PricingSuggestion:
    """Safe pricing guidance exposed to the UI."""

    produto: Product
    quantidade: int
    preco_sugerido_unitario: Decimal
    preco_minimo_unitario: Decimal
    total_sugerido: Decimal
    total_minimo: Decimal


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of a proposal validation."""

    produto: Product
    proposta: SaleProposal
    preco_minimo_unitario: Decimal
    total_bruto: Decimal
    total_liquido: Decimal


@dataclass(frozen=True, slots=True)
class SaleExecutionResult:
    """Persisted sale information after a successful transaction."""

    venda: Sale
    auditoria: AuditLog
    estoque_restante: int


def _money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _normalize_ratio(value: Decimal | str | int | float, field_name: str) -> Decimal:
    ratio = Decimal(str(value)).quantize(RATIO_STEP, rounding=ROUND_HALF_UP)
    if ratio < ZERO or ratio > ONE:
        raise InvalidProductError(f"{field_name} deve estar entre 0 e 1.")
    return ratio


def _coerce_origin(value: OriginType | str) -> OriginType:
    try:
        if isinstance(value, OriginType):
            return value
        return OriginType(str(value).strip().upper())
    except ValueError as exc:
        raise EngineError("Origem da proposta invalida.") from exc


def _coerce_negotiation_status(value: NegotiationStatus | str) -> NegotiationStatus:
    try:
        if isinstance(value, NegotiationStatus):
            return value
        return NegotiationStatus(str(value).strip().upper())
    except ValueError as exc:
        raise NegotiationStatusError("Status de negociacao invalido.") from exc


def _safe_origin(value: object) -> OriginType:
    try:
        return _coerce_origin(value)  # type: ignore[arg-type]
    except EngineError:
        return OriginType.SISTEMA


def _safe_negotiation_status(value: object) -> NegotiationStatus | None:
    if value is None:
        return None
    try:
        return _coerce_negotiation_status(value)  # type: ignore[arg-type]
    except EngineError:
        return None


def _normalize_quantity(quantidade: int) -> int:
    if quantidade <= 0:
        raise InvalidQuantityError("A quantidade deve ser maior que zero.")
    return quantidade


def _normalize_product(product: Product) -> Product:
    if not product.sku.strip():
        raise InvalidProductError("O SKU do produto e obrigatorio.")
    if not product.nome.strip():
        raise InvalidProductError("O nome do produto e obrigatorio.")
    if not product.categoria.strip():
        raise InvalidProductError("A categoria do produto e obrigatoria.")
    if product.estoque_atual < 0:
        raise InvalidProductError("O estoque nao pode ser negativo.")

    preco_custo = _money(product.preco_custo)
    preco_tabela = _money(product.preco_tabela)

    if preco_custo < ZERO:
        raise InvalidProductError("O preco de custo nao pode ser negativo.")
    if preco_tabela < ZERO:
        raise InvalidProductError("O preco de tabela nao pode ser negativo.")

    margem_ia = _normalize_ratio(product.margem_ia, "margem_ia")
    margem_minima = _normalize_ratio(product.margem_minima, "margem_minima")

    return Product(
        id=product.id,
        sku=product.sku.strip(),
        nome=product.nome.strip(),
        categoria=product.categoria.strip(),
        descricao=product.descricao.strip(),
        preco_custo=preco_custo,
        preco_tabela=preco_tabela,
        margem_ia=margem_ia,
        margem_minima=margem_minima,
        estoque_atual=int(product.estoque_atual),
        ativo=bool(product.ativo),
        criado_em=product.criado_em,
        atualizado_em=product.atualizado_em,
    )


def _normalize_proposal(proposal: SaleProposal) -> SaleProposal:
    return SaleProposal(
        produto_id=int(proposal.produto_id),
        quantidade=_normalize_quantity(int(proposal.quantidade)),
        preco_ofertado_unitario=_money(proposal.preco_ofertado_unitario),
        origem=_coerce_origin(proposal.origem),
        negociacao_status=_coerce_negotiation_status(proposal.negociacao_status),
        observacoes=proposal.observacoes.strip(),
    )


def calculate_minimum_price(product: Product) -> Decimal:
    """Return the deterministic minimum price accepted by the engine."""

    tabela_com_margem_ia = _money(product.preco_tabela * (ONE - product.margem_ia))
    custo_com_margem_minima = _money(product.preco_custo * (ONE + product.margem_minima))
    return max(tabela_com_margem_ia, custo_com_margem_minima)


def validate_stock(product: Product, quantidade: int) -> None:
    """Ensure that requested quantity is available."""

    quantidade_validada = _normalize_quantity(quantidade)
    if product.estoque_atual < quantidade_validada:
        raise InsufficientStockError(
            f"Estoque insuficiente para {product.nome}. Disponivel: {product.estoque_atual}, solicitado: {quantidade_validada}."
        )


def validate_negotiation_status(
    status: NegotiationStatus,
    *,
    allowed_statuses: tuple[NegotiationStatus, ...],
) -> None:
    """Ensure the FSM state allows the requested operation."""

    if status not in allowed_statuses:
        allowed = ", ".join(item.value for item in allowed_statuses)
        raise NegotiationStatusError(
            f"Status de negociacao invalido para esta operacao. Recebido: {status.value}. Permitidos: {allowed}."
        )


def _fetch_active_product(
    connection: sqlite3.Connection,
    product_id: int,
) -> Product:
    product = ProductRepository(connection).get_active_by_id(product_id)
    if product is None:
        raise ProductNotFoundError(f"Produto ativo nao encontrado para o id {product_id}.")
    return product


def _build_validation_context(
    proposal: SaleProposal,
    product: Product | None,
    *,
    preco_minimo_unitario: Decimal | None = None,
    total_bruto: Decimal | None = None,
    total_liquido: Decimal | None = None,
) -> dict[str, object]:
    return {
        "produto_id": proposal.produto_id,
        "quantidade": proposal.quantidade,
        "preco_ofertado_unitario": str(proposal.preco_ofertado_unitario),
        "origem": proposal.origem.value,
        "negociacao_status": proposal.negociacao_status.value,
        "observacoes": proposal.observacoes,
        "produto": {
            "sku": product.sku,
            "nome": product.nome,
            "categoria": product.categoria,
            "preco_custo": str(product.preco_custo),
            "preco_tabela": str(product.preco_tabela),
            "margem_ia": str(product.margem_ia),
            "margem_minima": str(product.margem_minima),
            "estoque_atual": product.estoque_atual,
        }
        if product
        else None,
        "preco_minimo_unitario": str(preco_minimo_unitario) if preco_minimo_unitario is not None else None,
        "total_bruto": str(total_bruto) if total_bruto is not None else None,
        "total_liquido": str(total_liquido) if total_liquido is not None else None,
    }


def _build_raw_proposal_context(proposal: SaleProposal) -> dict[str, object]:
    return {
        "produto_id": getattr(proposal, "produto_id", None),
        "quantidade": getattr(proposal, "quantidade", None),
        "preco_ofertado_unitario": str(getattr(proposal, "preco_ofertado_unitario", None)),
        "origem": getattr(getattr(proposal, "origem", None), "value", str(getattr(proposal, "origem", None))),
        "negociacao_status": getattr(
            getattr(proposal, "negociacao_status", None),
            "value",
            str(getattr(proposal, "negociacao_status", None)),
        ),
        "observacoes": getattr(proposal, "observacoes", ""),
    }


def _record_audit_outside_main_flow(
    *,
    entity: AuditEntity,
    entity_id: int | None,
    event: AuditEvent,
    origin: OriginType,
    decision: AuditDecision,
    negotiation_status: NegotiationStatus | None,
    reason: str,
    context: dict[str, object],
) -> None:
    try:
        with transaction() as connection:
            AuditRepository(connection).create(
                AuditLog(
                    entidade=entity,
                    entidade_id=entity_id,
                    evento=event,
                    origem=origin,
                    status_negociacao=negotiation_status,
                    decisao=decision,
                    motivo=reason,
                    contexto=context,
                )
            )
    except Exception:
        # Preserve the original engine error if audit persistence also fails.
        return


def register_product(
    product: Product,
    *,
    origin: OriginType = OriginType.HUMANO,
) -> Product:
    """Persist a validated product and record the operation in audit."""

    normalized_product = _normalize_product(product)

    try:
        with transaction() as connection:
            product_repository = ProductRepository(connection)
            audit_repository = AuditRepository(connection)

            created_product = product_repository.create(normalized_product)
            audit_repository.create(
                AuditLog(
                    entidade=AuditEntity.PRODUTO,
                    entidade_id=created_product.id,
                    evento=AuditEvent.PRODUTO_REGISTRADO,
                    origem=origin,
                    decisao=AuditDecision.REGISTRADA,
                    motivo="Produto registrado com sucesso na camada de persistencia.",
                    contexto={
                        "sku": created_product.sku,
                        "nome": created_product.nome,
                        "categoria": created_product.categoria,
                        "preco_custo": str(created_product.preco_custo),
                        "preco_tabela": str(created_product.preco_tabela),
                        "margem_ia": str(created_product.margem_ia),
                        "margem_minima": str(created_product.margem_minima),
                        "estoque_atual": created_product.estoque_atual,
                    },
                )
            )

            return created_product
    except sqlite3.IntegrityError as exc:
        raise InvalidProductError("Ja existe um produto cadastrado com o mesmo SKU.") from exc


def update_product(product: Product) -> Product:
    """Update a product through the deterministic engine."""

    normalized_product = _normalize_product(product)
    if normalized_product.id is None:
        raise InvalidProductError("O produto precisa ter id para ser atualizado.")

    try:
        with transaction() as connection:
            product_repository = ProductRepository(connection)
            audit_repository = AuditRepository(connection)

            existing = product_repository.get_by_id(normalized_product.id)
            if existing is None:
                raise ProductNotFoundError(
                    f"Produto nao encontrado para atualizacao: {normalized_product.id}."
                )

            updated_product = product_repository.update(normalized_product)
            audit_repository.create(
                AuditLog(
                    entidade=AuditEntity.PRODUTO,
                    entidade_id=updated_product.id,
                    evento=AuditEvent.PRODUTO_ATUALIZADO,
                    origem=OriginType.HUMANO,
                    decisao=AuditDecision.REGISTRADA,
                    motivo="Produto atualizado com sucesso na camada de persistencia.",
                    contexto={
                        "antes": {
                            "sku": existing.sku,
                            "nome": existing.nome,
                            "categoria": existing.categoria,
                            "preco_custo": str(existing.preco_custo),
                            "preco_tabela": str(existing.preco_tabela),
                            "margem_ia": str(existing.margem_ia),
                            "margem_minima": str(existing.margem_minima),
                            "estoque_atual": existing.estoque_atual,
                            "ativo": existing.ativo,
                        },
                        "depois": {
                            "sku": updated_product.sku,
                            "nome": updated_product.nome,
                            "categoria": updated_product.categoria,
                            "preco_custo": str(updated_product.preco_custo),
                            "preco_tabela": str(updated_product.preco_tabela),
                            "margem_ia": str(updated_product.margem_ia),
                            "margem_minima": str(updated_product.margem_minima),
                            "estoque_atual": updated_product.estoque_atual,
                            "ativo": updated_product.ativo,
                        },
                    },
                )
            )
            return updated_product
    except sqlite3.IntegrityError as exc:
        raise InvalidProductError("Ja existe um produto cadastrado com o mesmo SKU.") from exc


def deactivate_product(product_id: int, reason: str) -> Product:
    """Soft-delete a product and keep an audit trail."""

    if not reason.strip():
        raise InvalidProductError("Informe o motivo da exclusao do produto.")

    with transaction() as connection:
        product_repository = ProductRepository(connection)
        audit_repository = AuditRepository(connection)

        existing = product_repository.get_by_id(product_id)
        if existing is None:
            raise ProductNotFoundError(f"Produto nao encontrado para exclusao: {product_id}.")

        deactivated = product_repository.deactivate(product_id)
        if deactivated is None:
            raise ProductNotFoundError(f"Produto nao encontrado para exclusao: {product_id}.")

        audit_repository.create(
            AuditLog(
                entidade=AuditEntity.PRODUTO,
                entidade_id=deactivated.id,
                evento=AuditEvent.PRODUTO_DESATIVADO,
                origem=OriginType.HUMANO,
                decisao=AuditDecision.REGISTRADA,
                motivo=reason.strip(),
                contexto={
                    "sku": existing.sku,
                    "nome": existing.nome,
                    "categoria": existing.categoria,
                    "ativo_antes": existing.ativo,
                    "ativo_depois": deactivated.ativo,
                },
            )
        )

        return deactivated


def list_products(
    *,
    only_active: bool = False,
    search_term: str | None = None,
) -> list[Product]:
    """List products without leaking persistence details to the UI."""

    connection = get_connection()
    try:
        return ProductRepository(connection).list_products(
            only_active=only_active,
            search_term=search_term,
        )
    finally:
        connection.close()


def get_product(product_id: int) -> Product:
    """Load a single product from persistence."""

    connection = get_connection()
    try:
        product = ProductRepository(connection).get_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Produto nao encontrado: {product_id}.")
        return product
    finally:
        connection.close()


def build_pricing_suggestion(product_id: int, quantidade: int = 1) -> PricingSuggestion:
    """Generate a safe price suggestion based on the deterministic margins."""

    quantidade_validada = _normalize_quantity(quantidade)
    product = get_product(product_id)
    if not product.ativo:
        raise ProductNotFoundError(f"Produto inativo para sugestao: {product_id}.")

    preco_minimo_unitario = calculate_minimum_price(product)
    faixa_negociavel = product.preco_tabela - preco_minimo_unitario
    if faixa_negociavel > ZERO:
        preco_sugerido_unitario = _money(product.preco_tabela - (faixa_negociavel / Decimal("2")))
    else:
        preco_sugerido_unitario = preco_minimo_unitario

    return PricingSuggestion(
        produto=product,
        quantidade=quantidade_validada,
        preco_sugerido_unitario=_money(preco_sugerido_unitario),
        preco_minimo_unitario=preco_minimo_unitario,
        total_sugerido=_money(preco_sugerido_unitario * quantidade_validada),
        total_minimo=_money(preco_minimo_unitario * quantidade_validada),
    )


def validate_sale_proposal(
    proposal: SaleProposal,
    *,
    connection: sqlite3.Connection | None = None,
) -> ValidationResult:
    """Validate stock, minimum price and negotiation state."""

    owns_connection = connection is None
    active_connection = connection or get_connection()

    try:
        normalized_proposal = _normalize_proposal(proposal)
        validate_negotiation_status(
            normalized_proposal.negociacao_status,
            allowed_statuses=(NegotiationStatus.NEGOCIANDO, NegotiationStatus.CHECKOUT),
        )
        product = _fetch_active_product(active_connection, normalized_proposal.produto_id)
        validate_stock(product, normalized_proposal.quantidade)

        preco_minimo_unitario = calculate_minimum_price(product)
        if normalized_proposal.preco_ofertado_unitario < preco_minimo_unitario:
            raise PriceValidationError(
                "Preco ofertado abaixo do limite permitido. "
                f"Minimo aceito: {preco_minimo_unitario}, ofertado: {normalized_proposal.preco_ofertado_unitario}."
            )

        total_bruto = _money(product.preco_tabela * normalized_proposal.quantidade)
        total_liquido = _money(normalized_proposal.preco_ofertado_unitario * normalized_proposal.quantidade)

        return ValidationResult(
            produto=product,
            proposta=normalized_proposal,
            preco_minimo_unitario=preco_minimo_unitario,
            total_bruto=total_bruto,
            total_liquido=total_liquido,
        )
    finally:
        if owns_connection:
            active_connection.close()


def evaluate_proposal(proposal: SaleProposal) -> ValidationResult:
    """Evaluate a suggestion-layer proposal and persist an audit record."""

    try:
        with transaction() as connection:
            validation = validate_sale_proposal(proposal, connection=connection)
            AuditRepository(connection).create(
                AuditLog(
                    entidade=AuditEntity.NEGOCIACAO,
                    entidade_id=validation.produto.id,
                    evento=AuditEvent.PROPOSTA_VALIDADA,
                    origem=validation.proposta.origem,
                    status_negociacao=validation.proposta.negociacao_status,
                    decisao=AuditDecision.APROVADA,
                    motivo="Proposta validada pelo Motor de Regras.",
                    contexto=_build_validation_context(
                        validation.proposta,
                        validation.produto,
                        preco_minimo_unitario=validation.preco_minimo_unitario,
                        total_bruto=validation.total_bruto,
                        total_liquido=validation.total_liquido,
                    ),
                )
            )
            return validation
    except EngineError as exc:
        try:
            fallback_proposal = _normalize_proposal(proposal)
            fallback_context = _build_validation_context(fallback_proposal, None)
            fallback_status = fallback_proposal.negociacao_status
            fallback_origin = fallback_proposal.origem
            fallback_entity_id = fallback_proposal.produto_id
        except EngineError:
            fallback_context = _build_raw_proposal_context(proposal)
            fallback_status = _safe_negotiation_status(getattr(proposal, "negociacao_status", None))
            fallback_origin = _safe_origin(getattr(proposal, "origem", OriginType.SISTEMA))
            fallback_entity_id = getattr(proposal, "produto_id", None)
        _record_audit_outside_main_flow(
            entity=AuditEntity.NEGOCIACAO,
            entity_id=fallback_entity_id,
            event=AuditEvent.PROPOSTA_REJEITADA,
            origin=fallback_origin,
            decision=AuditDecision.REJEITADA,
            negotiation_status=fallback_status,
            reason=str(exc),
            context=fallback_context,
        )
        raise


def process_sale(proposal: SaleProposal) -> SaleExecutionResult:
    """Validate and persist a sale atomically."""

    try:
        normalized_proposal = _normalize_proposal(proposal)
        with transaction() as connection:
            validation = validate_sale_proposal(normalized_proposal, connection=connection)
            validate_negotiation_status(
                validation.proposta.negociacao_status,
                allowed_statuses=(NegotiationStatus.CHECKOUT,),
            )

            product_repository = ProductRepository(connection)
            sale_repository = SaleRepository(connection)
            audit_repository = AuditRepository(connection)

            updated_product = product_repository.decrease_stock_if_available(
                validation.produto.id or 0,
                validation.proposta.quantidade,
            )
            if updated_product is None:
                raise InsufficientStockError(
                    "Falha de concorrencia ao baixar o estoque. Tente novamente."
                )

            sale = sale_repository.create(
                Sale(
                    produto_id=validation.produto.id or 0,
                    sku=validation.produto.sku,
                    produto_nome=validation.produto.nome,
                    quantidade=validation.proposta.quantidade,
                    preco_tabela_unitario=validation.produto.preco_tabela,
                    preco_custo_unitario=validation.produto.preco_custo,
                    margem_ia=validation.produto.margem_ia,
                    margem_minima=validation.produto.margem_minima,
                    preco_minimo_unitario=validation.preco_minimo_unitario,
                    preco_ofertado_unitario=validation.proposta.preco_ofertado_unitario,
                    total_bruto=validation.total_bruto,
                    total_liquido=validation.total_liquido,
                    origem=validation.proposta.origem,
                    status=SaleStatus.CONCLUIDA,
                    negociacao_status=validation.proposta.negociacao_status,
                    observacoes=validation.proposta.observacoes,
                )
            )

            audit = audit_repository.create(
                AuditLog(
                    entidade=AuditEntity.VENDA,
                    entidade_id=sale.id,
                    evento=AuditEvent.VENDA_CONCLUIDA,
                    origem=sale.origem,
                    status_negociacao=sale.negociacao_status,
                    decisao=AuditDecision.APROVADA,
                    motivo="Venda concluida apos validacao deterministica e persistencia ACID.",
                    contexto=_build_validation_context(
                        validation.proposta,
                        validation.produto,
                        preco_minimo_unitario=validation.preco_minimo_unitario,
                        total_bruto=validation.total_bruto,
                        total_liquido=validation.total_liquido,
                    ),
                )
            )

            if validation.proposta.origem == OriginType.IA:
                previous_status = BusinessAuditLogger.classify_stock_status(validation.produto.estoque_atual)
                current_status = BusinessAuditLogger.classify_stock_status(updated_product.estoque_atual)
                if previous_status != current_status:
                    try:
                        BusinessAuditLogger.log_stock_status_change(
                            session_id=None,
                            product_name=validation.produto.nome,
                            previous_stock=validation.produto.estoque_atual,
                            current_stock=updated_product.estoque_atual,
                            previous_status=previous_status,
                            current_status=current_status,
                            reasoning=(
                                "Venda concluida com origem IA alterou a classificacao de estoque "
                                "apos baixa transacional ACID."
                            ),
                        )
                    except Exception:
                        pass

            return SaleExecutionResult(
                venda=sale,
                auditoria=audit,
                estoque_restante=updated_product.estoque_atual,
            )
    except EngineError as exc:
        try:
            fallback_proposal = _normalize_proposal(proposal)
            fallback_context = _build_validation_context(fallback_proposal, None)
            fallback_status = fallback_proposal.negociacao_status
            fallback_origin = fallback_proposal.origem
        except EngineError:
            fallback_context = _build_raw_proposal_context(proposal)
            fallback_status = _safe_negotiation_status(getattr(proposal, "negociacao_status", None))
            fallback_origin = _safe_origin(getattr(proposal, "origem", OriginType.SISTEMA))
        _record_audit_outside_main_flow(
            entity=AuditEntity.VENDA,
            entity_id=None,
            event=AuditEvent.VENDA_REJEITADA,
            origin=fallback_origin,
            decision=AuditDecision.REJEITADA,
            negotiation_status=fallback_status,
            reason=str(exc),
            context=fallback_context,
        )
        raise
