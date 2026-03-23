"""Settings screen for maintenance and data-protection operations."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.database.backup_manager import BackupError, BackupResult, create_backup, get_backup_directory
from src.database.connection import get_database_path


class BackupWorker(QThread):
    """Background worker that runs VACUUM INTO without freezing the UI."""

    backup_ready = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def run(self) -> None:
        try:
            result = create_backup()
        except BackupError as exc:
            self.error_occurred.emit(str(exc))
            return
        except Exception:
            self.error_occurred.emit("Falha inesperada ao gerar o backup ACID.")
            return

        self.backup_ready.emit(result)


class SettingsModuleWidget(QWidget):
    """Operational settings page focused on backup and local data safety."""

    backup_completed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backup_worker: BackupWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(18)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_box = QVBoxLayout()
        title_box.setSpacing(6)

        title = QLabel("Configuracoes")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Proteja o banco local com backups integrais e acompanhe os caminhos operacionais da instancia atual."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)

        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, stretch=1)
        root_layout.addWidget(header)

        panel = QFrame()
        panel.setObjectName("contentPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        panel_layout.setSpacing(14)

        backup_title = QLabel("Backup ACID do SQLite")
        backup_title.setObjectName("sectionTitle")

        description = QLabel(
            "O backup usa VACUUM INTO para gerar uma copia consistente do banco sem substituir o arquivo original."
        )
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)

        self._database_label = QLabel(f"Banco ativo: {get_database_path()}")
        self._database_label.setObjectName("statusText")
        self._database_label.setWordWrap(True)

        self._backup_dir_label = QLabel(f"Pasta de backups: {get_backup_directory()}")
        self._backup_dir_label.setObjectName("pageSubtitle")
        self._backup_dir_label.setWordWrap(True)

        self._backup_button = QPushButton("Fazer Backup Agora")
        self._backup_button.setObjectName("primaryButton")
        self._backup_button.clicked.connect(self._start_backup)

        self._backup_status_label = QLabel("Nenhum backup executado nesta sessao.")
        self._backup_status_label.setObjectName("statusText")
        self._backup_status_label.setWordWrap(True)

        panel_layout.addWidget(backup_title)
        panel_layout.addWidget(description)
        panel_layout.addWidget(self._database_label)
        panel_layout.addWidget(self._backup_dir_label)
        panel_layout.addWidget(self._backup_button)
        panel_layout.addWidget(self._backup_status_label)

        root_layout.addWidget(panel)
        root_layout.addStretch(1)

    def _start_backup(self) -> None:
        if self._backup_worker is not None and self._backup_worker.isRunning():
            return

        self._backup_button.setEnabled(False)
        self._backup_status_label.setText("Gerando backup ACID do SQLite...")

        self._backup_worker = BackupWorker()
        self._backup_worker.backup_ready.connect(self._handle_backup_ready)
        self._backup_worker.error_occurred.connect(self._handle_backup_error)
        self._backup_worker.finished.connect(self._on_backup_finished)
        self._backup_worker.start()

    def _handle_backup_ready(self, result: object) -> None:
        if not isinstance(result, BackupResult):
            self._handle_backup_error("Resultado invalido retornado pelo backup.")
            return

        self._backup_status_label.setText(
            f"Backup concluido em {result.backup_path} ({result.size_bytes} bytes)."
        )
        self.backup_completed.emit(str(result.backup_path))
        QMessageBox.information(
            self,
            "Backup concluido",
            f"Backup criado com sucesso em:\n{result.backup_path}",
        )

    def _handle_backup_error(self, message: str) -> None:
        self._backup_status_label.setText(message)
        QMessageBox.warning(self, "Backup", message)

    def _on_backup_finished(self) -> None:
        self._backup_button.setEnabled(True)
        if self._backup_worker is not None:
            self._backup_worker.deleteLater()
            self._backup_worker = None
