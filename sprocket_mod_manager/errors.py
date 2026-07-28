class ModManagerError(RuntimeError):
    """Base error shown to CLI and GUI users."""


class RegistryError(ModManagerError):
    pass


class DownloadError(ModManagerError):
    pass


class ResolutionError(ModManagerError):
    pass


class ScanError(ModManagerError):
    pass


class InstallError(ModManagerError):
    pass
