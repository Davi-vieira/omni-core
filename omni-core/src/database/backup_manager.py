"""ACID-safe SQLite backup utilities for Omni-Core ERP."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.database.connection import DB_PATH, get_connection, initialize_database
from src.utils.runtime import get_runtime_root

BACKUP_DIR = get_runtime_root() / "backups"


class BackupError(Exception):
    """Raised when an ACID-safe database backup cannot be generated."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Structured output for a successful SQLite backup."""

    backup_path: Path
    created_at: datetime
    size_bytes: int


def get_backup_directory() -> Path:
    """Return the writable directory used to store SQLite backups."""

    return BACKUP_DIR


def create_backup(destination_dir: Path | None = None) -> BackupResult:
    """Generate a full SQLite backup using ``VACUUM INTO``.

    The operation produces a consistent copy of the database file and validates
    the resulting backup with ``PRAGMA integrity_check`` before returning.
    """

    initialize_database()
    backup_root = (destination_dir or BACKUP_DIR).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now()
    backup_path = backup_root / f"omni_core_backup_{timestamp:%Y%m%d_%H%M%S}.db"
    if backup_path.exists():
        raise BackupError(f"Arquivo de backup ja existe: {backup_path}")

    connection = get_connection()
    try:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
        escaped_backup_path = str(backup_path).replace("'", "''")
        connection.execute(f"VACUUM INTO '{escaped_backup_path}'")
    except sqlite3.Error as exc:
        raise BackupError(f"Falha ao gerar o backup ACID do SQLite: {exc}") from exc
    finally:
        connection.close()

    _validate_backup_file(backup_path)
    return BackupResult(
        backup_path=backup_path,
        created_at=timestamp,
        size_bytes=backup_path.stat().st_size,
    )


def _validate_backup_file(backup_path: Path) -> None:
    if not backup_path.exists():
        raise BackupError(f"O backup nao foi criado em: {backup_path}")

    validation_connection = sqlite3.connect(backup_path)
    try:
        row = validation_connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"Nao foi possivel validar o backup gerado: {exc}") from exc
    finally:
        validation_connection.close()

    integrity_result = str(row[0] if row else "").strip().lower()
    if integrity_result != "ok":
        raise BackupError(
            f"Backup gerado com integridade invalida para {DB_PATH.name}: {integrity_result or 'sem resposta'}"
        )
