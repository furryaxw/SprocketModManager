"""WebView 能调到的方法必须是 `ClientApi` 上的**真方法**。

pywebview 建 JS 桥时按 `dir()` 枚举暴露对象，而 `ClientApi.__getattr__` 转发给控制器的那条路
它看不见：夹具里直接调 `api.run_diagnosis()` 是通的，界面里却是 `API unavailable`。
这条把「JS 引用的每个名字都在 `ClientApi` 上」钉住 —— 那种不一致只有真窗口才看得见，
所以只能在这里拦。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.presentation.web_gui import ClientApi

ROOT = Path(__file__).resolve().parents[1]
CLIENT_UI_JS = ROOT / "sprocket_mod_manager" / "presentation" / "client_ui" / "js"

# 两种走法：`callApi("name")`，以及直接摸桥的 `window.pywebview?.api?.name?.(…)`。
CALL_API = re.compile(r'callApi\(\s*"([A-Za-z0-9_]+)"')
DIRECT = re.compile(r"pywebview\??\.api\??\.([A-Za-z0-9_]+)")


def referenced_api_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(CLIENT_UI_JS.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        names.update(CALL_API.findall(source))
        names.update(DIRECT.findall(source))
    return names


class WebViewApiSurfaceTests(unittest.TestCase):
    def test_the_scan_finds_the_names_the_client_calls(self) -> None:
        names = referenced_api_names()

        self.assertGreater(len(names), 20)
        self.assertIn("run_diagnosis", names)
        self.assertIn("client_log", names, "直接摸桥的那种走法也要扫到")

    def test_every_name_the_client_calls_is_a_method_on_the_api_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ConfigStore(Path(directory)).save({"language": "en", "game_path": "", "index_url": ""})
            api = ClientApi("test", app_dir=Path(directory))
            try:
                exposed = set(dir(api))
            finally:
                api.install_queue.close()

        missing = sorted(referenced_api_names() - exposed)

        self.assertEqual(
            missing,
            [],
            "这些名字没有声明在 ClientApi 上：WebView 里会报 `API unavailable`",
        )

    def test_getattr_reaches_more_than_dir_which_is_why_the_scan_is_the_guard(self) -> None:
        """控制器上有些同名方法**不是** API 面：`getattr` 拿得到、`dir()` 看不到。

        所以拦这个洞只能看「JS 引用了什么」那一侧 —— 对比控制器名单会把它们当成漏声明。
        """
        with tempfile.TemporaryDirectory() as directory:
            ConfigStore(Path(directory)).save({"language": "en", "game_path": "", "index_url": ""})
            api = ClientApi("test", app_dir=Path(directory))
            try:
                self.assertNotIn("catalog_payload", dir(api))
                self.assertTrue(callable(getattr(api, "catalog_payload")))
            finally:
                api.install_queue.close()


if __name__ == "__main__":
    unittest.main()
