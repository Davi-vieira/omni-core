"""Business-protection logger for IA commercial recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock

from src.utils.runtime import get_runtime_root

LOG_DIR = get_runtime_root() / "logs"
LOG_FILE = LOG_DIR / "ia_business_audit.log"
LOW_STOCK_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class BusinessAuditEntry:
    """Structured IA business-protection log entry."""

    timestamp: datetime
    action: str
    reasoning: str
    value: str

    def to_line(self) -> str:
        """Render the entry in the required textual audit format."""

        return (
            f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] | "
            f"[{self.action}] | "
            f"[{self.reasoning}] | "
            f"[{self.value}]"
        )


class BusinessAuditLogger:
    """Append-only logger for high-signal IA business actions."""

    _lock = Lock()

    @classmethod
    def log_discount_suggestion(
        cls,
        *,
        session_id: str,
        product_name: str,
        table_price: Decimal,
        minimum_price: Decimal,
        proposed_price: Decimal,
        reasoning: str,
    ) -> None:
        cls._write_entry(
            BusinessAuditEntry(
                timestamp=datetime.now(),
                action=f"DESCONTO_IA_SUGERIDO sessao={session_id} produto={product_name}",
                reasoning=reasoning.strip() or "Sugestao abaixo do preco de tabela dentro do piso deterministico.",
                value=(
                    f"tabela={table_price:.2f}; minimo={minimum_price:.2f}; "
                    f"proposto={proposed_price:.2f}"
                ),
            )
        )

    @classmethod
    def log_stock_status_change(
        cls,
        *,
        session_id: str | None,
        product_name: str,
        previous_stock: int,
        current_stock: int,
        previous_status: str,
        current_status: str,
        reasoning: str,
    ) -> None:
        cls._write_entry(
            BusinessAuditEntry(
                timestamp=datetime.now(),
                action=(
                    f"STATUS_ESTOQUE_IA sessao={session_id or 'indisponivel'} "
                    f"produto={product_name}"
                ),
                reasoning=reasoning.strip() or "Venda assistida pela IA alterou a classificacao de estoque.",
                value=(
                    f"estoque={previous_stock}->{current_stock}; "
                    f"status={previous_status}->{current_status}"
                ),
            )
        )

    @staticmethod
    def classify_stock_status(stock: int) -> str:
        """Return the semantic stock status used by the business-protection layer."""

        if stock <= 0:
            return "SEM_ESTOQUE"
        if stock <= LOW_STOCK_THRESHOLD:
            return "CRITICO"
        if stock <= 10:
            return "BAIXO"
        return "OK"

    @classmethod
    def _write_entry(cls, entry: BusinessAuditEntry) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_line())
                handle.write("\n")
