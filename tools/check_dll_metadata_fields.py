"""检查部署后的 DLL 里 DLL 元数据字段现状：声明的 modId、必需依赖、不兼容程序集。

只读（静态 PE 解析，不执行 DLL 代码）。这里只报告规范化后的字段。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
GAME = Path(r"G:\Sprocket")
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sprocket_mod_manager.application.local_mods import scan_local_mods  # noqa: E402


def main() -> int:
    mods = scan_local_mods(GAME, {}, ())
    print(f"{'path':<46} {'declared id':<34} {'requires':<28} conflicts")
    print("-" * 150)
    for mod in mods:
        print(f"{mod.path:<46} {mod.declared_id or '-':<34} "
              f"{','.join(mod.required_dependencies) or '-':<28} "
              f"{','.join(mod.incompatible_assemblies) or '-'}")
    print()
    print(f"DLL 数量：{len(mods)}")
    print(f"依赖声明汇总：{[(m.path, list(m.required_dependencies)) for m in mods if m.required_dependencies]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
