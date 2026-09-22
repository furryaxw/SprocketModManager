"""直接读 DLL 里真正写进去的 Sprocket.Mod.Id（用管理器的静态 PE 解析，不执行 DLL 代码）。

用来验证"改源码 + 重新构建 + 部署"之后，磁盘上的 DLL 身份是否真的变了——
比看源码或看构建日志可靠。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
GAME = Path(r"G:\Sprocket")
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sprocket_mod_manager.application.local_mods import scan_local_mods  # noqa: E402

if __name__ == "__main__":
    mods = scan_local_mods(GAME, {}, ())
    print(f"{'path':<48} {'declared Sprocket.Mod.Id':<42} name")
    print("-" * 120)
    for mod in mods:
        print(f"{mod.path:<48} {mod.declared_id or '-':<42} {mod.display_name}")
    declared = sum(1 for mod in mods if mod.declared_id)
    print(f"\n声明了 Sprocket.Mod.Id 的 DLL：{declared}/{len(mods)}")
