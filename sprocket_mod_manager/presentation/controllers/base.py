from __future__ import annotations

from typing import Any


class ApiController:
    def __init__(self, api: Any):
        object.__setattr__(self, "api", api)

    def __getattribute__(self, name: str) -> Any:
        if name not in {"api", "__class__", "__dict__", "__getattr__", "__setattr__", "__delattr__"}:
            api = object.__getattribute__(self, "api")
            if name in vars(api):
                return vars(api)[name]
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.api, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.api, name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self.api, name)
