"""Security Module"""
__version__ = "1.0.0"
__author__ = "Omni-Core Team"

from .license_manager import (
    LICENSE_FILENAME,
    LicenseCollectionError,
    LicenseError,
    LicenseManager,
    LicenseValidationError,
    LicenseValidationResult,
)

__all__ = [
    "LICENSE_FILENAME",
    "LicenseCollectionError",
    "LicenseError",
    "LicenseManager",
    "LicenseValidationError",
    "LicenseValidationResult",
]
