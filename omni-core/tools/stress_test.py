"""Concurrent webhook stress test for the Omni-Core integration server."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.network import DEFAULT_GATEWAY_PORT


@dataclass(frozen=True, slots=True)
class StressResult:
    """Single request result from the stress test."""

    client_id: int
    status_code: int
    elapsed_seconds: float
    ok: bool
    reply_text: str


def _post_json(url: str, payload: dict[str, object], timeout: float) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=timeout) as http_response:
        return http_response.status, http_response.read().decode("utf-8")


def _simulate_client(client_id: int, url: str, timeout: float) -> StressResult:
    payload = {
        "provider": "stress_test",
        "session_id": f"stress-client-{client_id}",
        "sender": f"5511999900{client_id:02d}",
        "message": f"Ola, tem estoque do produto de teste {client_id}?",
    }

    started_at = time.perf_counter()
    try:
        status_code, body = _post_json(url, payload, timeout)
        payload_json = json.loads(body)
        ok = bool(payload_json.get("ok"))
        reply_text = str(payload_json.get("reply_text") or payload_json.get("reply") or "")
    except error.HTTPError as exc:
        status_code = exc.code
        ok = False
        reply_text = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        status_code = 0
        ok = False
        reply_text = str(exc)

    return StressResult(
        client_id=client_id,
        status_code=status_code,
        elapsed_seconds=time.perf_counter() - started_at,
        ok=ok,
        reply_text=reply_text,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dispara requisicoes simultaneas contra o /webhook do Omni-Core."
    )
    parser.add_argument(
        "--clients",
        type=int,
        default=10,
        help="Quantidade de clientes simultaneos.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_GATEWAY_PORT,
        help="Porta do servidor local.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Timeout individual por requisicao em segundos.",
    )
    parser.add_argument(
        "--url",
        default="",
        help="URL completa do webhook. Se omitida, usa o host local padrao.",
    )
    args = parser.parse_args()
    clients = max(1, args.clients)

    url = args.url.strip() or f"http://127.0.0.1:{args.port}/webhook"
    print(f"[stress] Iniciando carga contra {url}")
    print(f"[stress] Clientes simultaneos: {clients}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(_simulate_client, client_id, url, args.timeout)
            for client_id in range(1, clients + 1)
        ]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    ordered_results = sorted(results, key=lambda item: item.client_id)
    elapsed_samples = [item.elapsed_seconds for item in ordered_results]
    success_count = sum(1 for item in ordered_results if item.ok)

    for result in ordered_results:
        print(
            "[stress] "
            f"cliente={result.client_id:02d} "
            f"status={result.status_code} "
            f"ok={result.ok} "
            f"tempo={result.elapsed_seconds:.3f}s "
            f"reply={result.reply_text[:90]}"
        )

    print("[stress] Resumo:")
    print(f"[stress] Sucessos: {success_count}/{len(ordered_results)}")
    print(f"[stress] Tempo medio: {statistics.mean(elapsed_samples):.3f}s")
    print(f"[stress] Tempo maximo: {max(elapsed_samples):.3f}s")


if __name__ == "__main__":
    main()
