"""Network integration layer for Omni-Core."""

from .whatsapp_gateway import (
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
    GatewayError,
    WebhookPayloadError,
    WhatsAppGatewayThread,
)

__all__ = [
    "DEFAULT_GATEWAY_HOST",
    "DEFAULT_GATEWAY_PORT",
    "GatewayError",
    "WebhookPayloadError",
    "WhatsAppGatewayThread",
]
