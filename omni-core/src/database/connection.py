"""SQLite connection and schema management for Omni-Core ERP."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Iterator

from src.utils.runtime import get_bundle_data_dir, get_runtime_data_dir, get_runtime_root

sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))
sqlite3.register_converter("DECIMAL", lambda value: Decimal(value.decode("utf-8")))

PROJECT_ROOT = get_runtime_root()
APPDATA_VENDOR_DIR = "OmniCore"
BUNDLED_DATA_DIR = get_bundle_data_dir()
SCHEMA_VERSION = 4

_SCHEMA_LOCK = Lock()
_SCHEMA_READY = False

_CREATE_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS produtos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL UNIQUE,
        nome TEXT NOT NULL,
        categoria TEXT NOT NULL DEFAULT '',
        descricao TEXT NOT NULL DEFAULT '',
        preco_custo DECIMAL NOT NULL CHECK (preco_custo >= 0),
        preco_tabela DECIMAL NOT NULL CHECK (preco_tabela >= 0),
        margem_ia DECIMAL NOT NULL DEFAULT 0.00 CHECK (margem_ia >= 0 AND margem_ia <= 1),
        margem_minima DECIMAL NOT NULL DEFAULT 0.00 CHECK (margem_minima >= 0 AND margem_minima <= 1),
        estoque_atual INTEGER NOT NULL DEFAULT 0 CHECK (estoque_atual >= 0),
        ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vendas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produto_id INTEGER NOT NULL,
        sku TEXT NOT NULL,
        produto_nome TEXT NOT NULL,
        quantidade INTEGER NOT NULL CHECK (quantidade > 0),
        preco_tabela_unitario DECIMAL NOT NULL CHECK (preco_tabela_unitario >= 0),
        preco_custo_unitario DECIMAL NOT NULL CHECK (preco_custo_unitario >= 0),
        margem_ia DECIMAL NOT NULL CHECK (margem_ia >= 0 AND margem_ia <= 1),
        margem_minima DECIMAL NOT NULL DEFAULT 0.00 CHECK (margem_minima >= 0 AND margem_minima <= 1),
        preco_minimo_unitario DECIMAL NOT NULL CHECK (preco_minimo_unitario >= 0),
        preco_ofertado_unitario DECIMAL NOT NULL CHECK (preco_ofertado_unitario >= 0),
        total_bruto DECIMAL NOT NULL CHECK (total_bruto >= 0),
        total_liquido DECIMAL NOT NULL CHECK (total_liquido >= 0),
        origem TEXT NOT NULL DEFAULT 'HUMANO' CHECK (origem IN ('IA', 'HUMANO', 'SISTEMA')),
        status TEXT NOT NULL DEFAULT 'CONCLUIDA' CHECK (status IN ('PENDENTE', 'CONCLUIDA', 'CANCELADA', 'ESTORNADA')),
        negociacao_status TEXT NOT NULL DEFAULT 'CHECKOUT' CHECK (negociacao_status IN ('AGUARDANDO', 'PESQUISA', 'NEGOCIANDO', 'CHECKOUT', 'CONCLUIDA', 'CANCELADA')),
        observacoes TEXT NOT NULL DEFAULT '',
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (produto_id) REFERENCES produtos(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidade TEXT NOT NULL CHECK (entidade IN ('PRODUTO', 'VENDA', 'NEGOCIACAO', 'SISTEMA')),
        entidade_id INTEGER,
        evento TEXT NOT NULL,
        origem TEXT NOT NULL CHECK (origem IN ('IA', 'HUMANO', 'SISTEMA')),
        status_negociacao TEXT CHECK (status_negociacao IS NULL OR status_negociacao IN ('AGUARDANDO', 'PESQUISA', 'NEGOCIANDO', 'CHECKOUT', 'CONCLUIDA', 'CANCELADA')),
        decisao TEXT NOT NULL CHECK (decisao IN ('APROVADA', 'REJEITADA', 'REGISTRADA')),
        motivo TEXT NOT NULL DEFAULT '',
        contexto_json TEXT NOT NULL DEFAULT '{}',
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT NOT NULL,
        atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS atendimentos_ia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
        content TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_produtos_sku ON produtos(sku)",
    "CREATE INDEX IF NOT EXISTS idx_vendas_produto_id ON vendas(produto_id)",
    "CREATE INDEX IF NOT EXISTS idx_vendas_criado_em ON vendas(criado_em)",
    "CREATE INDEX IF NOT EXISTS idx_vendas_status ON vendas(status)",
    "CREATE INDEX IF NOT EXISTS idx_auditoria_entidade ON auditoria(entidade, entidade_id)",
    "CREATE INDEX IF NOT EXISTS idx_auditoria_criado_em ON auditoria(criado_em)",
    "CREATE INDEX IF NOT EXISTS idx_atendimentos_ia_session ON atendimentos_ia(session_id, criado_em)",
)

_MIGRATION_COLUMNS = {
    "produtos": {
        "categoria": "ALTER TABLE produtos ADD COLUMN categoria TEXT NOT NULL DEFAULT ''",
        "margem_minima": "ALTER TABLE produtos ADD COLUMN margem_minima DECIMAL NOT NULL DEFAULT 0.00",
    },
    "vendas": {
        "sku": "ALTER TABLE vendas ADD COLUMN sku TEXT NOT NULL DEFAULT ''",
        "produto_nome": "ALTER TABLE vendas ADD COLUMN produto_nome TEXT NOT NULL DEFAULT ''",
        "margem_minima": "ALTER TABLE vendas ADD COLUMN margem_minima DECIMAL NOT NULL DEFAULT 0.00",
        "negociacao_status": "ALTER TABLE vendas ADD COLUMN negociacao_status TEXT NOT NULL DEFAULT 'CHECKOUT'",
        "observacoes": "ALTER TABLE vendas ADD COLUMN observacoes TEXT NOT NULL DEFAULT ''",
    },
}


@dataclass(frozen=True, slots=True)
class SalesKpis:
    """Sales aggregates shown by the dashboard."""

    faturamento_hoje: Decimal
    faturamento_mes: Decimal
    vendas_hoje: int
    vendas_mes: int
    ticket_medio_hoje: Decimal
    ticket_medio_mes: Decimal


def _is_frozen_runtime() -> bool:
    """Return ``True`` when running from a frozen executable bundle."""

    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def _resolve_frozen_data_dir() -> Path:
    """Return a writable per-user data directory for frozen runs."""

    appdata_root = os.getenv("APPDATA", "").strip()
    if appdata_root:
        return Path(appdata_root).expanduser().resolve() / APPDATA_VENDOR_DIR / "data"
    return get_runtime_data_dir()


def _resolve_runtime_database_path() -> Path:
    """Return the effective SQLite path for the current runtime."""

    data_dir = _resolve_frozen_data_dir() if _is_frozen_runtime() else get_runtime_data_dir()
    return data_dir / "omni_core.db"


DB_PATH = _resolve_runtime_database_path()
DATA_DIR = DB_PATH.parent
SEED_DB_PATH = BUNDLED_DATA_DIR / "omni_core.db"


def _ensure_database_directory() -> Path:
    """Ensure that the database directory exists before opening SQLite."""

    db_dir = DB_PATH.parent
    os.makedirs(db_dir, exist_ok=True)
    return db_dir


def _open_connection() -> sqlite3.Connection:
    _ensure_database_directory()
    connection = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=30.0,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _prepare_database_file() -> None:
    _ensure_database_directory()
    if DB_PATH.exists():
        return
    if not SEED_DB_PATH.exists():
        return
    if SEED_DB_PATH.resolve() == DB_PATH.resolve():
        return
    shutil.copy2(SEED_DB_PATH, DB_PATH)


def _existing_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _apply_schema(connection: sqlite3.Connection) -> None:
    for statement in _CREATE_TABLE_STATEMENTS:
        connection.execute(statement)

    for table_name, migrations in _MIGRATION_COLUMNS.items():
        columns = _existing_columns(connection, table_name)
        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(statement)

    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def get_database_path() -> Path:
    """Return the writable database path for the active runtime."""

    return DB_PATH


def initialize_database() -> Path:
    """Create and migrate the SQLite database using ACID-safe DDL."""

    global _SCHEMA_READY

    if _SCHEMA_READY and DB_PATH.exists():
        return DB_PATH

    with _SCHEMA_LOCK:
        if _SCHEMA_READY and DB_PATH.exists():
            return DB_PATH

        _prepare_database_file()
        connection = _open_connection()

        try:
            connection.execute("BEGIN EXCLUSIVE")
            _apply_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        _SCHEMA_READY = True
        return DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a configured SQLite connection."""

    initialize_database()
    return _open_connection()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Provide an explicit ACID transaction using BEGIN IMMEDIATE."""

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def fetch_sales_kpis(reference_date: date | None = None) -> SalesKpis:
    """Read aggregated sales KPIs for the dashboard."""

    initialize_database()
    current_day = reference_date or date.today()
    month_start = current_day.replace(day=1)

    query = """
        SELECT
            COALESCE(SUM(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS faturamento_hoje,
            COALESCE(SUM(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS faturamento_mes,
            COALESCE(SUM(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN 1 ELSE 0 END), 0) AS vendas_hoje,
            COALESCE(SUM(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN 1 ELSE 0 END), 0) AS vendas_mes,
            COALESCE(AVG(CASE WHEN DATE(criado_em) = ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS ticket_medio_hoje,
            COALESCE(AVG(CASE WHEN DATE(criado_em) >= ? AND status = 'CONCLUIDA' THEN total_liquido END), 0) AS ticket_medio_mes
        FROM vendas
    """

    connection = get_connection()

    try:
        row = connection.execute(
            query,
            (
                current_day.isoformat(),
                month_start.isoformat(),
                current_day.isoformat(),
                month_start.isoformat(),
                current_day.isoformat(),
                month_start.isoformat(),
            ),
        ).fetchone()
    finally:
        connection.close()

    return SalesKpis(
        faturamento_hoje=_to_decimal(row["faturamento_hoje"]),
        faturamento_mes=_to_decimal(row["faturamento_mes"]),
        vendas_hoje=int(row["vendas_hoje"] or 0),
        vendas_mes=int(row["vendas_mes"] or 0),
        ticket_medio_hoje=_to_decimal(row["ticket_medio_hoje"]),
        ticket_medio_mes=_to_decimal(row["ticket_medio_mes"]),
    )


initialize_database()
