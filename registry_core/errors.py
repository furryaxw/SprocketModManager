class ModManagerError(RuntimeError):
    """Base error shown to CLI and GUI users."""


class RegistryError(ModManagerError):
    pass


class DownloadError(ModManagerError):
    pass


class ResolutionError(ModManagerError):
    """解不出一组自洽的版本。

    `conflicts` 里是卡住的那个包，以及各自对它提要求的来源（包 id + 版本区间）：上层
    （计划页）要靠它说清冲突双方，并据此决定让谁出局、让谁的需求让位。
    """

    def __init__(self, message: str, conflicts: tuple[dict, ...] = ()):
        super().__init__(message)
        self.conflicts = conflicts


class ScanError(ModManagerError):
    pass


class InstallError(ModManagerError):
    pass


class InstallConflictError(InstallError):
    code = "file_conflict"


class ModToggleError(ModManagerError):
    pass


class CatalogBusyError(ModManagerError):
    code = "catalog_busy"
