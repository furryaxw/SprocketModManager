"""Core package for the Sprocket Mod Manager."""

from .errors import (
    DownloadError,
    InstallError,
    ModManagerError,
    RegistryError,
    ResolutionError,
    ScanError,
)
from .models import RegistryPackage, ReleaseAsset, ReleaseInfo, ResolutionPlan
from .semver import Version, satisfies

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
