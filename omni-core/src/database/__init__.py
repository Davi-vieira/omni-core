"""Database access layer for Omni-Core ERP."""

from .connection import (
    DB_PATH,
    PROJECT_ROOT,
    SalesKpis,
    fetch_sales_kpis,
    get_connection,
    get_database_path,
    initialize_database,
    transaction,
)
from .repositories import (
    AuditRepository,
    ChatHistoryRepository,
    DashboardRepository,
    ProductRepository,
    SaleRepository,
    SettingsRepository,
)

__all__ = [
    "AuditRepository",
    "ChatHistoryRepository",
    "DashboardRepository",
    "DB_PATH",
    "PROJECT_ROOT",
    "ProductRepository",
    "SaleRepository",
    "SettingsRepository",
    "SalesKpis",
    "fetch_sales_kpis",
    "get_connection",
    "get_database_path",
    "initialize_database",
    "transaction",
]
