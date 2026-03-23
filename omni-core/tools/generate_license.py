"""Administrative tool to generate a hardware-bound ``license.key`` file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.security import LicenseManager


def main() -> int:
    """Generate a ``license.key`` file from a provided HWID."""

    parser = argparse.ArgumentParser(
        description="Gera uma licenca SHA-256 para o HWID informado."
    )
    parser.add_argument(
        "hwid",
        nargs="?",
        help="HWID bruto a ser convertido em license.key.",
    )
    parser.add_argument(
        "--current",
        action="store_true",
        help="Usa o HWID da maquina atual em vez de um valor manual.",
    )
    parser.add_argument(
        "--output",
        default=str(LicenseManager.get_license_path()),
        help="Caminho do arquivo license.key que sera gerado.",
    )
    args = parser.parse_args()

    if args.current:
        hwid = LicenseManager.collect_hwid()
    elif args.hwid:
        hwid = args.hwid.strip()
    else:
        parser.error("Informe um HWID manual ou use --current.")

    output_path = Path(args.output).expanduser().resolve()
    LicenseManager.write_license_key(hwid, output_path=output_path)

    print(f"HWID: {hwid}")
    print(f"SHA-256: {LicenseManager.compute_hwid_hash(hwid)}")
    print(f"Arquivo gerado em: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
