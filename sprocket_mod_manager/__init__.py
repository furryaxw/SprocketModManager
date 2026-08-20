"""Core package for the Sprocket Mod Manager."""

import logging

from .domain.errors import (
    DownloadError,
    InstallError,
    ModManagerError,
    RegistryError,
    ResolutionError,
    ScanError,
)
from .domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo, ResolutionPlan
from .domain.semver import Version, satisfies

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "DownloadError",
    "InstallError",
    "ModManagerError",
    "RegistryError",
    "RegistryPackage",
    "ReleaseAsset",
    "ReleaseInfo",
    "ResolutionError",
    "ResolutionPlan",
    "ScanError",
    "Version",
    "satisfies",
]
