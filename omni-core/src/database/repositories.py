"""SQLite repositories for Omni-Core ERP persistence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.database.connection import SalesKpis
from src.domain.models import (
    AuditLog,
    Product,
    Sale,
)

MONEY_STEP = Decimal("0.01")
RATIO_STEP = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class AnalyticsPoint:
    """Daily analytics datapoint rendered by the dashboard charts."""

    label: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class StockSuggestionItem:
    """Low-stock product frequently suggested by the IA in the current day."""

    product_id: int
    nome: str
    categoria: str
    estoque_atual: int
    suggestion_count: int


@dataclass(frozen=True, slots=True)
class DashboardAnalyticsSnapshot:
    """Combined analytics payload consumed by the dashboard UI."""

    vendas_hoje_total: Decimal
    atendimentos_ia: int
    conversao_ia_percentual: Decimal
    faturamento_diario_ia: list[AnalyticsPoint]
    ticket_medio_ia: list[AnalyticsPoint]
    estoque_critico_sugerido: list[StockSuggestionItem]


@dataclass(frozen=True, slots=True)
class ChatHistoryMessage:
    """Persisted IA attendance message used to hydrate short-term memory."""

    session_id: str
    role: str
    content: str
    metadata: dict[str, Any]
    id: int | None = None
    criado_em: datetime | None = None


def _money(value: Decimal | str | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _ratio(value: Decimal | str | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(RATIO_STEP, rounding=ROUND_HALF_UP)


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace(" ", "T"))


class ProductRepository:
    """Persistence operations for product records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(self, product: Product) -> Product:
        cursor = self.connection.execute(
            """
            INSERT INTO produtos (
                sku,
                nome,
                categoria,
                descricao,
                preco_custo,
                preco_tabela,
                margem_ia,
                margem_minima,
                estoque_atual,
                ativo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product.sku,
                product.nome,
                product.categoria,
                product.descricao,
                product.preco_custo,
                product.preco_tabela,
                product.margem_ia,
                product.margem_minima,
                product.estoque_atual,
                int(product.ativo),
            ),
        )
        created = self.get_by_id(int(cursor.lastrowid))
        if created is None:
            raise RuntimeError("Falha ao materializar o produto recem-criado.")
        return created

    def update(self, product: Product) -> Product:
        if product.id is None:
            raise ValueError("Produto precisa ter id para ser atualizado.")

        cursor = self.connection.execute(
            """
            UPDATE produtos
            SET
                sku = ?,
                nome = ?,
                categoria = ?,
                descricao = ?,
                preco_custo = ?,
                preco_tabela = ?,
                margem_ia = ?,
                margem_minima = ?,
                estoque_atual = ?,
                ativo = ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                product.sku,
                product.nome,
                product.categoria,
                product.descricao,
                product.preco_custo,
                product.preco_tabela,
                product.margem_ia,
                product.margem_minima,
                product.estoque_atual,
                int(product.ativo),
                product.id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Falha ao atualizar o produto.")
        updated = self.get_by_id(product.id)
        if updated is None:
            raise RuntimeError("Produto nao encontrado apos atualizacao.")
        return updated

    def get_by_id(self, product_id: int) -> Product | None:
        row = self.connection.execute(
            "SELECT * FROM produtos WHERE id = ?",
            (product_id,),
        ).fetchone()
        return self._map_row(row)

    def get_active_by_id(self, product_id: int) -> Product | None:
        row = self.connection.execute(
            "SELECT * FROM produtos WHERE id = ? AND ativo = 1",
            (product_id,),
        ).fetchone()
        return self._map_row(row)

    def get_by_sku(self, sku: str) -> Product | None:
        row = self.connection.execute(
            "SELECT * FROM produtos WHERE sku = ?",
            (sku,),
        ).fetchone()
        return self._map_row(row)

    def list_products(
        self,
        *,
        only_active: bool = False,
        search_term: str | None = None,
    ) -> list[Product]:
        query = """
            SELECT *
            FROM produtos
            WHERE (? = 0 OR ativo = 1)
              AND (
                    ? IS NULL
                    OR TRIM(?) = ''
                    OR nome LIKE ?
                    OR sku LIKE ?
                    OR categoria LIKE ?
                )
            ORDER BY nome COLLATE NOCASE ASC
        """
        normalized_search = (search_term or "").strip()
        like_term = f"%{normalized_search}%"
        rows = self.connection.execute(
            query,
            (
                int(only_active),
                normalized_search if normalized_search else None,
                normalized_search,
                like_term,
                like_term,
                like_term,
            ),
        ).fetchall()
        return [product for row in rows if (product := self._map_row(row)) is not None]

    def deactivate(self, product_id: int) -> Product | None:
        cursor = self.connection.execute(
            """
            UPDATE produtos
            SET ativo = 0, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (product_id,),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_by_id(product_id)

    def decrease_stock_if_available(self, product_id: int, quantity: int) -> Product | None:
        cursor = self.connection.execute(
            """
            UPDATE produtos
            SET estoque_atual = estoque_atual - ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ? AND ativo = 1 AND estoque_atual >= ?
            """,
            (quantity, product_id, quantity),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_active_by_id(product_id)

    @staticmethod
    def _map_row(row: sqlite3.Row | None) -> Product | None:
        if row is None:
            return None
        return Product(
            id=int(row["id"]),
            sku=str(row["sku"]),
            nome=str(row["nome"]),
            categoria=str(row["categoria"]),
            descricao=str(row["descricao"]),
            preco_custo=_money(row["preco_custo"]),
            preco_tabela=_money(row["preco_tabela"]),
            margem_ia=_ratio(row["margem_ia"]),
            margem_minima=_ratio(row["margem_minima"]),
            estoque_atual=int(row["estoque_atual"]),
            ativo=bool(row["ativo"]),
            criado_em=_timestamp(row["criado_em"]),
            atualizado_em=_timestamp(row["atualizado_em"]),
        )


class SaleRepository:
    """Persistence operations for sales."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(self, sale: Sale) -> Sale:
        cursor = self.connection.execute(
            """
            INSERT INTO vendas (
                produto_id,
                sku,
                produto_nome,
                quantidade,
                preco_tabela_unitario,
                preco_custo_unitario,
                margem_ia,
                margem_minima,
                preco_minimo_unitario,
                preco_ofertado_unitario,
                total_bruto,
                total_liquido,
                origem,
                status,
                negociacao_status,
                observacoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sale.produto_id,
                sale.sku,
                sale.produto_nome,
                sale.quantidade,
                sale.preco_tabela_unitario,
                sale.preco_custo_unitario,
                sale.margem_ia,
                sale.margem_minima,
                sale.preco_minimo_unitario,
                sale.preco_ofertado_unitario,
                sale.total_bruto,
                sale.total_liquido,
                sale.origem.value,
                sale.status.value,
                sale.negociacao_status.value,
                sale.observacoes,
            ),
        )
        created = self.get_by_id(int(cursor.lastrowid))
        if created is None:
            raise RuntimeError("Falha ao materializar a venda recem-criada.")
        return created

    def get_by_id(self, sale_id: int) -> Sale | None:
        row = self.connection.execute(
            "SELECT * FROM vendas WHERE id = ?",
            (sale_id,),
        ).fetchone()
        return self._map_row(row)

    @staticmethod
    def _map_row(row: sqlite3.Row | None) -> Sale | None:
        if row is None:
            return None
        from src.domain.models import NegotiationStatus, OriginType, SaleStatus

        return Sale(
            id=int(row["id"]),
            produto_id=int(row["produto_id"]),
            sku=str(row["sku"]),
            produto_nome=str(row["produto_nome"]),
            quantidade=int(row["quantidade"]),
            preco_tabela_unitario=_money(row["preco_tabela_unitario"]),
            preco_custo_unitario=_money(row["preco_custo_unitario"]),
            margem_ia=_ratio(row["margem_ia"]),
            margem_minima=_ratio(row["margem_minima"]),
            preco_minimo_unitario=_money(row["preco_minimo_unitario"]),
            preco_ofertado_unitario=_money(row["preco_ofertado_unitario"]),
            total_bruto=_money(row["total_bruto"]),
            total_liquido=_money(row["total_liquido"]),
            origem=OriginType(str(row["origem"])),
            status=SaleStatus(str(row["status"])),
            negociacao_status=NegotiationStatus(str(row["negociacao_status"])),
            observacoes=str(row["observacoes"]),
            criado_em=_timestamp(row["criado_em"]),
        )


class AuditRepository:
    """Persistence operations for immutable audit records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(self, audit_log: AuditLog) -> AuditLog:
        cursor = self.connection.execute(
            """
            INSERT INTO auditoria (
                entidade,
                entidade_id,
                evento,
                origem,
                status_negociacao,
                decisao,
                motivo,
                contexto_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_log.entidade.value,
                audit_log.entidade_id,
                audit_log.evento.value,
                audit_log.origem.value,
                audit_log.status_negociacao.value if audit_log.status_negociacao else None,
                audit_log.decisao.value,
                audit_log.motivo,
                json.dumps(audit_log.contexto, ensure_ascii=True, sort_keys=True, default=str),
            ),
        )
        created = self.get_by_id(int(cursor.lastrowid))
        if created is None:
            raise RuntimeError("Falha ao materializar a trilha de auditoria.")
        return created

    def get_by_id(self, audit_id: int) -> AuditLog | None:
        row = self.connection.execute(
            "SELECT * FROM auditoria WHERE id = ?",
            (audit_id,),
        ).fetchone()
        return self._map_row(row)

    @staticmethod
    def _map_row(row: sqlite3.Row | None) -> AuditLog | None:
        if row is None:
            return None
        from src.domain.models import (
            AuditDecision,
            AuditEntity,
            AuditEvent,
            NegotiationStatus,
            OriginType,
        )

        status_value = row["status_negociacao"]
        contexto: dict[str, Any] = json.loads(str(row["contexto_json"]))

        return AuditLog(
            id=int(row["id"]),
            entidade=AuditEntity(str(row["entidade"])),
            entidade_id=int(row["entidade_id"]) if row["entidade_id"] is not None else None,
            evento=AuditEvent(str(row["evento"])),
            origem=OriginType(str(row["origem"])),
            status_negociacao=NegotiationStatus(str(status_value)) if status_value else None,
            decisao=AuditDecision(str(row["decisao"])),
            motivo=str(row["motivo"]),
            contexto=contexto,
            criado_em=_timestamp(row["criado_em"]),
        )


class SettingsRepository:
    """Persistence operations for application-level settings."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_value(self, key: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT valor
            FROM configuracoes
            WHERE chave = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return str(row["valor"])

    def set_value(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO configuracoes (chave, valor, atualizado_em)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chave) DO UPDATE SET
                valor = excluded.valor,
                atualizado_em = CURRENT_TIMESTAMP
            """,
            (key, value),
        )


class ChatHistoryRepository:
    """Persistence operations for IA attendance conversations."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatHistoryMessage:
        cursor = self.connection.execute(
            """
            INSERT INTO atendimentos_ia (
                session_id,
                role,
                content,
                metadata_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                session_id.strip(),
                role.strip().lower(),
                content,
                json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True, default=str),
            ),
        )
        created = self.get_by_id(int(cursor.lastrowid))
        if created is None:
            raise RuntimeError("Falha ao materializar a mensagem persistida do atendimento IA.")
        return created

    def list_recent_messages(
        self,
        *,
        session_id: str,
        limit: int = 8,
    ) -> list[ChatHistoryMessage]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM atendimentos_ia
                WHERE session_id = ?
                ORDER BY criado_em DESC, id DESC
                LIMIT ?
            )
            ORDER BY criado_em ASC, id ASC
            """,
            (session_id.strip(), max(1, int(limit))),
        ).fetchall()
        return [message for row in rows if (message := self._map_row(row)) is not None]

    def get_by_id(self, message_id: int) -> ChatHistoryMessage | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM atendimentos_ia
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
        return self._map_row(row)

    @staticmethod
    def _map_row(row: sqlite3.Row | None) -> ChatHistoryMessage | None:
        if row is None:
            return None
        metadata_raw = str(row["metadata_json"] or "{}")
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata = {"raw_metadata": metadata_raw}
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        return ChatHistoryMessage(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            metadata=metadata,
            criado_em=_timestamp(row["criado_em"]),
        )


class DashboardRepository:
    """Read-only dashboard metrics."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def fetch_sales_kpis(self, reference_date: date | None = None) -> SalesKpis:
        current_day = reference_date or date.today()
        month_start = current_day.replace(day=1)

        row = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS faturamento_hoje,
                COALESCE(SUM(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS faturamento_mes,
                COALESCE(SUM(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN 1 ELSE 0 END), 0) AS vendas_hoje,
                COALESCE(SUM(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN 1 ELSE 0 END), 0) AS vendas_mes,
                COALESCE(AVG(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS ticket_medio_hoje,
                COALESCE(AVG(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS ticket_medio_mes
            FROM vendas
            """,
            (
                current_day.isoformat(),
                month_start.isoformat(),
                current_day.isoformat(),
                month_start.isoformat(),
                current_day.isoformat(),
                month_start.isoformat(),
            ),
        ).fetchone()

        return SalesKpis(
            faturamento_hoje=_money(row["faturamento_hoje"]),
            faturamento_mes=_money(row["faturamento_mes"]),
            vendas_hoje=int(row["vendas_hoje"] or 0),
            vendas_mes=int(row["vendas_mes"] or 0),
            ticket_medio_hoje=_money(row["ticket_medio_hoje"]),
            ticket_medio_mes=_money(row["ticket_medio_mes"]),
        )

    def fetch_analytics_snapshot(
        self,
        *,
        reference_date: date | None = None,
        days: int = 7,
        critical_stock_threshold: int = 3,
    ) -> DashboardAnalyticsSnapshot:
        current_day = reference_date or date.today()
        range_days = max(1, days)
        start_day = current_day - timedelta(days=range_days - 1)

        atendimentos_ia = self._fetch_ia_attendance_count(current_day)
        conversoes_ia = self._fetch_ia_converted_sales_count(current_day)
        conversion_rate = Decimal("0.00")
        if atendimentos_ia > 0:
            conversion_rate = (
                (Decimal(conversoes_ia) * Decimal("100")) / Decimal(atendimentos_ia)
            ).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)

        return DashboardAnalyticsSnapshot(
            vendas_hoje_total=self._fetch_sales_today_total(current_day),
            atendimentos_ia=atendimentos_ia,
            conversao_ia_percentual=conversion_rate,
            faturamento_diario_ia=self._fetch_daily_ia_revenue_series(start_day, current_day),
            ticket_medio_ia=self._fetch_daily_ia_ticket_series(start_day, current_day),
            estoque_critico_sugerido=self._fetch_critical_stock_suggestions(
                current_day,
                critical_stock_threshold,
            ),
        )

    def _fetch_sales_today_total(self, current_day: date) -> Decimal:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(total_liquido), 0) AS vendas_hoje_total
            FROM vendas
            WHERE DATE(criado_em) = ? AND status = 'CONCLUIDA'
            """,
            (current_day.isoformat(),),
        ).fetchone()
        return _money(row["vendas_hoje_total"])

    def _fetch_ia_attendance_count(self, current_day: date) -> int:
        rows = self.connection.execute(
            """
            SELECT contexto_json
            FROM auditoria
            WHERE DATE(criado_em) = ?
              AND origem = 'IA'
              AND entidade = 'NEGOCIACAO'
            """,
            (current_day.isoformat(),),
        ).fetchall()

        session_ids: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(str(row["contexto_json"]))
            except json.JSONDecodeError:
                continue
            session_id = str(payload.get("session_id") or "").strip()
            if session_id:
                session_ids.add(session_id)
        return len(session_ids)

    def _fetch_ia_converted_sales_count(self, current_day: date) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM vendas
            WHERE DATE(criado_em) = ?
              AND status = 'CONCLUIDA'
              AND origem = 'IA'
            """,
            (current_day.isoformat(),),
        ).fetchone()
        return int(row["total"] or 0)

    def _fetch_daily_ia_revenue_series(
        self,
        start_day: date,
        end_day: date,
    ) -> list[AnalyticsPoint]:
        rows = self.connection.execute(
            """
            SELECT DATE(criado_em) AS dia, COALESCE(SUM(total_liquido), 0) AS valor
            FROM vendas
            WHERE DATE(criado_em) BETWEEN ? AND ?
              AND status = 'CONCLUIDA'
              AND origem = 'IA'
            GROUP BY DATE(criado_em)
            ORDER BY DATE(criado_em) ASC
            """,
            (start_day.isoformat(), end_day.isoformat()),
        ).fetchall()
        return self._fill_daily_series(start_day, end_day, rows, value_key="valor")

    def _fetch_daily_ia_ticket_series(
        self,
        start_day: date,
        end_day: date,
    ) -> list[AnalyticsPoint]:
        rows = self.connection.execute(
            """
            SELECT DATE(criado_em) AS dia, COALESCE(AVG(total_liquido), 0) AS valor
            FROM vendas
            WHERE DATE(criado_em) BETWEEN ? AND ?
              AND status = 'CONCLUIDA'
              AND origem = 'IA'
            GROUP BY DATE(criado_em)
            ORDER BY DATE(criado_em) ASC
            """,
            (start_day.isoformat(), end_day.isoformat()),
        ).fetchall()
        return self._fill_daily_series(start_day, end_day, rows, value_key="valor")

    def _fetch_critical_stock_suggestions(
        self,
        current_day: date,
        critical_stock_threshold: int,
    ) -> list[StockSuggestionItem]:
        rows = self.connection.execute(
            """
            SELECT
                p.id AS product_id,
                p.nome AS nome,
                p.categoria AS categoria,
                p.estoque_atual AS estoque_atual,
                COUNT(a.id) AS suggestion_count
            FROM produtos p
            INNER JOIN auditoria a
                ON a.entidade = 'NEGOCIACAO'
               AND a.entidade_id = p.id
            WHERE DATE(a.criado_em) = ?
              AND a.origem = 'IA'
              AND a.evento = 'PROPOSTA_ATUALIZADA'
              AND p.ativo = 1
              AND p.estoque_atual < ?
            GROUP BY p.id, p.nome, p.categoria, p.estoque_atual
            ORDER BY suggestion_count DESC, p.estoque_atual ASC, p.nome ASC
            LIMIT 5
            """,
            (current_day.isoformat(), critical_stock_threshold),
        ).fetchall()

        return [
            StockSuggestionItem(
                product_id=int(row["product_id"]),
                nome=str(row["nome"]),
                categoria=str(row["categoria"]),
                estoque_atual=int(row["estoque_atual"]),
                suggestion_count=int(row["suggestion_count"] or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _fill_daily_series(
        start_day: date,
        end_day: date,
        rows: list[sqlite3.Row],
        *,
        value_key: str,
    ) -> list[AnalyticsPoint]:
        values_by_day = {
            str(row["dia"]): _money(row[value_key])
            for row in rows
        }

        points: list[AnalyticsPoint] = []
        cursor = start_day
        while cursor <= end_day:
            points.append(
                AnalyticsPoint(
                    label=cursor.strftime("%d/%m"),
                    value=values_by_day.get(cursor.isoformat(), Decimal("0.00")),
                )
            )
            cursor += timedelta(days=1)
        return points
