"""Hardware-bound license validation for Omni-Core ERP."""

from __future__ import annotations

import hashlib
import hmac
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.utils.runtime import get_runtime_root

LICENSE_FILENAME = "license.key"
_MASKED_HASH_PREFIX = 12
_WINDOWS_NO_WINDOW = 0x08000000
_INVALID_MARKERS = {
    "",
    "NONE",
    "NULL",
    "UNKNOWN",
    "N/A",
    "NOT APPLICABLE",
    "TO BE FILLED BY O.E.M.",
    "TO BE FILLED BY OEM",
}


class LicenseError(Exception):
    """Base exception for license handling."""


class LicenseCollectionError(LicenseError):
    """Raised when the current machine identifiers cannot be collected."""


class LicenseValidationError(LicenseError):
    """Raised when the current machine is not authorized."""


@dataclass(frozen=True, slots=True)
class LicenseValidationResult:
    """Structured output for the current license validation."""

    authorized: bool
    current_hwid: str
    current_hash: str
    expected_hash: str | None
    license_path: Path
    reason: str


class LicenseManager:
    """Validate the current machine against a hardware-bound license file."""

    @staticmethod
    def get_license_path() -> Path:
        """Return the license path next to the current runtime."""

        return get_runtime_root() / LICENSE_FILENAME

    @classmethod
    def collect_hwid(cls) -> str:
        """Collect a deterministic HWID from motherboard and processor identifiers."""

        board_identifier = cls._read_hardware_identifier(
            "System UUID",
            (
                ("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1 -ExpandProperty UUID)"),
                ("wmic", "csproduct", "get", "uuid"),
                ("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_BaseBoard | Select-Object -First 1 -ExpandProperty SerialNumber)"),
                ("wmic", "baseboard", "get", "serialnumber"),
            ),
        )
        processor_identifier = cls._read_hardware_identifier(
            "Processor ID",
            (
                ("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty ProcessorId)"),
                ("wmic", "cpu", "get", "ProcessorId"),
            ),
        )

        return f"BOARD={board_identifier}|CPU={processor_identifier}"

    @staticmethod
    def compute_hwid_hash(hwid: str) -> str:
        """Return the SHA-256 hash for a raw HWID string."""

        normalized_hwid = hwid.strip().upper()
        if not normalized_hwid:
            raise LicenseCollectionError("HWID vazio nao pode ser convertido em licenca.")
        return hashlib.sha256(normalized_hwid.encode("utf-8")).hexdigest()

    @classmethod
    def load_expected_hash(cls, license_path: Path | None = None) -> str:
        """Read the stored license hash from disk."""

        resolved_path = license_path or cls.get_license_path()
        if not resolved_path.exists():
            raise LicenseValidationError(
                f"Arquivo de licenca nao encontrado em: {resolved_path}"
            )

        license_hash = resolved_path.read_text(encoding="utf-8").strip().lower()
        if len(license_hash) != 64 or any(char not in "0123456789abcdef" for char in license_hash):
            raise LicenseValidationError("Arquivo de licenca invalido ou corrompido.")
        return license_hash

    @classmethod
    def validate_current_machine(cls) -> LicenseValidationResult:
        """Validate the current machine against ``license.key``."""

        license_path = cls.get_license_path()
        current_hwid = cls.collect_hwid()
        current_hash = cls.compute_hwid_hash(current_hwid)

        try:
            expected_hash = cls.load_expected_hash(license_path)
        except LicenseValidationError as exc:
            return LicenseValidationResult(
                authorized=False,
                current_hwid=current_hwid,
                current_hash=current_hash,
                expected_hash=None,
                license_path=license_path,
                reason=str(exc),
            )

        authorized = hmac.compare_digest(current_hash, expected_hash)
        reason = "Hardware validado com sucesso." if authorized else "HWID divergente da licenca cadastrada."
        return LicenseValidationResult(
            authorized=authorized,
            current_hwid=current_hwid,
            current_hash=current_hash,
            expected_hash=expected_hash,
            license_path=license_path,
            reason=reason,
        )

    @classmethod
    def assert_current_machine_authorized(cls) -> LicenseValidationResult:
        """Raise a validation error when the current hardware is not licensed."""

        result = cls.validate_current_machine()
        if not result.authorized:
            raise LicenseValidationError(result.reason)
        return result

    @classmethod
    def write_license_key(
        cls,
        hwid: str,
        *,
        output_path: Path | None = None,
    ) -> Path:
        """Write a ``license.key`` file for the provided HWID."""

        destination = output_path or cls.get_license_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"{cls.compute_hwid_hash(hwid)}\n", encoding="utf-8")
        return destination

    @staticmethod
    def mask_hash(hash_value: str) -> str:
        """Return a short representation suitable for debug logs."""

        if not hash_value:
            return "indisponivel"
        return f"{hash_value[:_MASKED_HASH_PREFIX]}..."

    @classmethod
    def _read_hardware_identifier(
        cls,
        label: str,
        commands: tuple[tuple[str, ...], ...],
    ) -> str:
        for command in commands:
            raw_value = cls._run_command(command)
            normalized_value = cls._normalize_identifier(raw_value)
            if normalized_value:
                return normalized_value

        raise LicenseCollectionError(
            f"Nao foi possivel coletar o identificador de hardware: {label}."
        )

    @staticmethod
    def _run_command(command: tuple[str, ...]) -> str:
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=8,
                creationflags=_WINDOWS_NO_WINDOW,
                startupinfo=startupinfo,
            )
        except (OSError, subprocess.SubprocessError):
            return ""

        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()

    @staticmethod
    def _normalize_identifier(raw_value: str) -> str:
        lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
        filtered_lines = [
            line for line in lines
            if line.upper() not in {"UUID", "SERIALNUMBER", "PROCESSORID"}
        ]
        if not filtered_lines:
            return ""

        candidate = filtered_lines[-1].replace(" ", "").strip().upper()
        if candidate in _INVALID_MARKERS:
            return ""
        if set(candidate) == {"0"}:
            return ""
        return candidate
