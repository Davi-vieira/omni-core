"""Open an ngrok tunnel for the embedded Omni-Core integration server."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from pyngrok import ngrok
    from pyngrok.exception import PyngrokError
except ImportError as exc:  # pragma: no cover - tool dependency
    raise SystemExit(
        "pyngrok nao esta instalado. Execute: pip install pyngrok"
    ) from exc

from src.network import DEFAULT_GATEWAY_PORT


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Abre um tunel ngrok para o webhook local do Omni-Core."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_GATEWAY_PORT,
        help="Porta local do servidor de integracao.",
    )
    parser.add_argument(
        "--authtoken",
        default=os.getenv("NGROK_AUTHTOKEN", "").strip() or None,
        help="Token opcional do ngrok. Se omitido, usa NGROK_AUTHTOKEN.",
    )
    args = parser.parse_args()

    if args.authtoken:
        ngrok.set_auth_token(args.authtoken)

    try:
        tunnel = ngrok.connect(addr=args.port, proto="http")
    except PyngrokError as exc:
        raise SystemExit(
            "Nao foi possivel abrir o tunel ngrok. "
            "Verifique se sua conta esta validada e se o authtoken foi configurado. "
            f"Detalhes: {exc}"
        ) from exc
    public_url = tunnel.public_url.rstrip("/")
    webhook_url = f"{public_url}/webhook"

    print(f"[ngrok] Tunel local aberto para a porta {args.port}.")
    print(f"[ngrok] Health local: http://127.0.0.1:{args.port}/health")
    print(f"[ngrok] Webhook publico: {webhook_url}")
    print("[ngrok] Mantenha esta janela aberta enquanto o bot do WhatsApp estiver em teste.")
    print("[ngrok] Pressione Ctrl+C para encerrar o tunel.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[ngrok] Encerrando tunel...")
    finally:
        ngrok.disconnect(public_url)
        ngrok.kill()


if __name__ == "__main__":
    main()
