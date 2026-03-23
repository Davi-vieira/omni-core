"""Lightweight WhatsApp webhook gateway for Omni-Core."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any

from flask import Flask, jsonify, request
from PyQt6.QtCore import QThread, pyqtSignal
from werkzeug.serving import make_server

from src.ia import BrainError, OmniBrain, SAFE_MANUAL_MESSAGE

DEFAULT_GATEWAY_HOST = os.getenv("OMNICORE_GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
DEFAULT_GATEWAY_PORT = int(os.getenv("OMNICORE_GATEWAY_PORT", "8787"))

_MESSAGE_KEYS = (
    "message",
    "text",
    "body",
    "conversation",
    "content",
    "caption",
    "query",
    "question",
)
_SESSION_KEYS = (
    "session_id",
    "conversation_id",
    "chat_id",
    "remotejid",
    "remote_jid",
    "jid",
    "from",
    "sender",
    "author",
    "participant",
    "phone",
    "contact",
)
_PROVIDER_KEYS = ("provider", "source", "gateway", "platform", "integration")
_SANITIZE_SESSION_PATTERN = re.compile(r"[^A-Za-z0-9_.:@-]+")


class GatewayError(Exception):
    """Base exception for the WhatsApp gateway."""


class WebhookPayloadError(GatewayError):
    """Raised when an incoming webhook payload is invalid."""


@dataclass(frozen=True, slots=True)
class WebhookEnvelope:
    """Normalized webhook data extracted from provider payloads."""

    session_id: str
    message_text: str
    provider: str
    raw_payload: dict[str, Any]


class WhatsAppGatewayThread(QThread):
    """Embedded HTTP gateway that proxies WhatsApp webhooks to OmniBrain."""

    status_changed = pyqtSignal(bool, str)
    request_processed = pyqtSignal(str, str)

    def __init__(
        self,
        *,
        host: str = DEFAULT_GATEWAY_HOST,
        port: int = DEFAULT_GATEWAY_PORT,
        parent: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._port = port
        self._brain = OmniBrain()
        self._brain_lock = Lock()
        self._server = None
        self._stop_requested = False
        self._app = self._build_flask_app()

    @property
    def host(self) -> str:
        """Return the configured host."""

        return self._host

    @property
    def port(self) -> int:
        """Return the configured port."""

        return self._port

    @property
    def health_url(self) -> str:
        """Return the local health endpoint URL."""

        return f"http://{self._host}:{self._port}/health"

    @property
    def webhook_url(self) -> str:
        """Return the local webhook endpoint URL."""

        return f"http://{self._host}:{self._port}/webhook"

    def run(self) -> None:
        """Start the embedded webhook server inside this QThread."""

        try:
            self._server = make_server(
                self._host,
                self._port,
                self._app,
                threaded=True,
            )
        except OSError as exc:
            self.status_changed.emit(
                False,
                f"Servidor de integracao indisponivel na porta {self._port}: {exc}",
            )
            return

        self.status_changed.emit(True, f"Servidor online em {self.webhook_url}")

        try:
            self._server.serve_forever()
        except Exception as exc:
            if not self._stop_requested:
                self.status_changed.emit(False, f"Servidor interrompido: {exc}")
        finally:
            if self._server is not None:
                try:
                    self._server.server_close()
                except Exception:
                    pass
                self._server = None
            self.status_changed.emit(False, "Servidor de integracao offline.")

    def stop(self) -> None:
        """Stop the embedded server gracefully."""

        self._stop_requested = True
        if self._server is not None:
            self._server.shutdown()

    def _build_flask_app(self) -> Flask:
        app = Flask(__name__)
        app.json.ensure_ascii = False

        @app.get("/health")
        def health() -> Any:
            return jsonify(
                {
                    "ok": True,
                    "status": "online",
                    "host": self._host,
                    "port": self._port,
                    "webhook_url": self.webhook_url,
                }
            )

        @app.post("/webhook")
        def webhook() -> Any:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Payload JSON invalido.",
                            "reply_text": SAFE_MANUAL_MESSAGE,
                            "safe_mode": True,
                        }
                    ),
                    400,
                )

            try:
                envelope = self._parse_webhook_payload(payload)
            except WebhookPayloadError as exc:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": str(exc),
                            "reply_text": SAFE_MANUAL_MESSAGE,
                            "safe_mode": True,
                        }
                    ),
                    400,
                )

            try:
                with self._brain_lock:
                    response = self._brain.process_message(
                        envelope.message_text,
                        envelope.session_id,
                    )
            except BrainError:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "provider": envelope.provider,
                            "session_id": envelope.session_id,
                            "reply_text": SAFE_MANUAL_MESSAGE,
                            "safe_mode": True,
                        }
                    ),
                    200,
                )

            self.request_processed.emit(envelope.session_id, response.intent.value)

            response_payload = response.to_payload()
            response_payload.update(
                {
                    "ok": True,
                    "provider": envelope.provider,
                    "reply": response.reply_text,
                    "message": response.reply_text,
                    "input_text": envelope.message_text,
                }
            )
            return jsonify(response_payload)

        return app

    def _parse_webhook_payload(self, payload: dict[str, Any]) -> WebhookEnvelope:
        message_text = self._find_first_scalar(payload, _MESSAGE_KEYS)
        if not message_text:
            raise WebhookPayloadError("Mensagem recebida sem texto processavel.")

        raw_session_id = self._find_first_scalar(payload, _SESSION_KEYS)
        provider = self._find_first_scalar(payload, _PROVIDER_KEYS) or "generic"
        session_id = self._normalize_session_id(raw_session_id or f"{provider}-session")

        return WebhookEnvelope(
            session_id=session_id,
            message_text=message_text,
            provider=provider,
            raw_payload=payload,
        )

    @classmethod
    def _find_first_scalar(cls, payload: Any, keys: tuple[str, ...]) -> str | None:
        normalized_keys = {item.lower() for item in keys}
        stack: list[Any] = [payload]

        while stack:
            current = stack.pop(0)
            if isinstance(current, dict):
                for key, value in current.items():
                    if key.lower() in normalized_keys:
                        scalar = cls._coerce_scalar(value)
                        if scalar:
                            return scalar
                    if isinstance(value, (dict, list, tuple)):
                        stack.append(value)
            elif isinstance(current, (list, tuple)):
                stack.extend(current)

        return None

    @staticmethod
    def _coerce_scalar(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        if isinstance(value, (int, float)):
            return str(value)
        return None

    @classmethod
    def _normalize_session_id(cls, raw_value: str) -> str:
        normalized = cls._coerce_scalar(raw_value) or "gateway-session"
        normalized = _SANITIZE_SESSION_PATTERN.sub("_", normalized).strip("_")
        return normalized or "gateway-session"
