from __future__ import annotations

import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from tkinter import filedialog

from .config import (
    ConfigStore,
    detect_game_path,
    detect_language,
    effective_game_path,
    effective_index_url,
)
from .dialogs import ask_confirmation, show_message
from .errors import DownloadError, ModManagerError
from .github import RepositoryRelease
from .models import RegistryPackage, ReleaseInfo, ResolutionPlan
from .semver import Version
from .service import DEFAULT_INDEX_URL, ModManagerService


COLORS = {
    "canvas": "#101213",
    "sidebar": "#131617",
    "surface": "#171A1B",
    "surface_high": "#1D2122",
    "surface_hover": "#242829",
    "line": "#303536",
    "line_soft": "#252A2B",
    "text": "#F0F2EF",
    "text_soft": "#BCC2BE",
    "muted": "#7E8782",
    "accent": "#DD5B43",
    "accent_hover": "#EB6B52",
    "accent_quiet": "#3A221E",
    "green": "#69B982",
    "green_quiet": "#17271D",
    "danger": "#A9443D",
    "danger_hover": "#C15148",
}

CATEGORY_KEYS = {
    "all": "category_all",
    "gameplay": "category_gameplay",
    "utility": "category_utility",
    "library": "category_library",
    "visual": "category_visual",
    "audio": "category_audio",
    "other": "category_other",
}

MANAGER_REPOSITORY = "furryaxw/sprocket-mods"

TEXT = {
    "en": {
        "title": "Sprocket Mod Manager",
        "brand_registry": "OPEN-SOURCE REGISTRY",
        "browse": "Catalog",
        "installed": "Installed",
        "settings": "Settings",
        "catalog_title": "Mod catalog",
        "catalog_subtitle": "Packages sourced directly from GitHub Releases",
        "installed_title": "Managed installation",
        "installed_subtitle": "Packages tracked for the selected Sprocket directory",
        "settings_title": "Application settings",
        "settings_subtitle": "Game location and registry source",
        "search": "Search name, author or tag",
        "refresh": "Refresh",
        "install": "Install",
        "update": "Update",
        "remove": "Remove",
        "update_all": "Update all",
        "repository": "Repository",
        "version": "Version",
        "authors": "Authors",
        "license": "License",
        "dependencies": "Dependencies",
        "assets": "Install assets",
        "category": "Category",
        "game_path": "Sprocket game path",
        "index_url": "Registry index URL or local path",
        "steam_not_found": "Sprocket was not found through Steam",
        "language": "Interface language",
        "language_auto": "Auto",
        "save": "Save settings",
        "browse_path": "Browse",
        "select_mod": "Select a package",
        "select_mod_hint": "Release, dependencies and install actions appear here.",
        "no_mods": "No matching packages",
        "no_installed": "No manager-installed packages",
        "loading": "Loading Registry and GitHub Releases...",
        "ready": "Registry ready - {count} packages",
        "installed_state": "Installed {version}",
        "dependency_state": "Dependency {version}",
        "unavailable": "No compatible Release",
        "confirm_install": "Apply this install plan?\n\n{plan}",
        "confirm_remove": "Remove {name}? Unused dependency packages will also be removed.",
        "settings_saved": "Settings saved",
        "install_done": "Installed {name}",
        "remove_done": "Removed: {names}",
        "up_to_date": "All requested packages are up to date",
        "updates_done": "Updated {count} package(s)",
        "game_path_required": "Set a valid Sprocket game path in Settings first.",
        "available": "Available {version}",
        "resolving": "Resolving {name}...",
        "package_count": "{count} packages",
        "installed_count": "{count} managed packages",
        "registry_connected": "REGISTRY CONNECTED",
        "registry_loading": "REGISTRY SYNC",
        "category_all": "All categories",
        "category_gameplay": "Gameplay",
        "category_utility": "Utility",
        "category_library": "Libraries",
        "category_visual": "Visual",
        "category_audio": "Audio",
        "category_other": "Other",
        "package_id": "PACKAGE ID",
        "release_state": "RELEASE STATE",
        "package_info": "PACKAGE INFORMATION",
        "dependency_info": "DEPENDENCY REQUIREMENTS",
        "requested": "Requested package",
        "dependency": "Installed dependency",
        "not_set": "Not set",
        "version_label": "VERSION {version}",
        "dialog_confirm": "CONFIRM ACTION",
        "dialog_warning": "ATTENTION",
        "dialog_error": "ERROR",
        "confirm": "Confirm",
        "cancel": "Cancel",
        "close": "Close",
        "manager_update_title": "Manager update available",
        "manager_update_eyebrow": "UPDATE AVAILABLE",
        "manager_update_message": "Version {latest} is available. You are using {current}.",
        "view_release": "View release",
        "later": "Later",
    },
    "zh": {
        "title": "Sprocket 模组管理器",
        "brand_registry": "开源模组目录",
        "browse": "模组目录",
        "installed": "已安装",
        "settings": "设置",
        "catalog_title": "模组目录",
        "catalog_subtitle": "软件包直接取自 GitHub Releases",
        "installed_title": "安装管理",
        "installed_subtitle": "当前 Sprocket 目录中由管理器追踪的软件包",
        "settings_title": "应用设置",
        "settings_subtitle": "游戏位置与 Registry 来源",
        "search": "搜索名称、作者或标签",
        "refresh": "刷新",
        "install": "安装",
        "update": "更新",
        "remove": "卸载",
        "update_all": "全部更新",
        "repository": "查看仓库",
        "version": "版本",
        "authors": "作者",
        "license": "许可证",
        "dependencies": "依赖",
        "assets": "安装资产",
        "category": "分类",
        "game_path": "Sprocket 游戏路径",
        "index_url": "Registry 索引 URL 或本地路径",
        "steam_not_found": "未能通过 Steam 找到 Sprocket",
        "language": "界面语言",
        "language_auto": "自动",
        "save": "保存设置",
        "browse_path": "浏览",
        "select_mod": "选择一个模组",
        "select_mod_hint": "这里会显示 Release、依赖与安装操作。",
        "no_mods": "没有匹配的软件包",
        "no_installed": "没有由管理器安装的软件包",
        "loading": "正在读取 Registry 与 GitHub Releases...",
        "ready": "Registry 就绪 - 共 {count} 个模组",
        "installed_state": "已安装 {version}",
        "dependency_state": "依赖安装 {version}",
        "unavailable": "没有兼容的 Release",
        "confirm_install": "确认执行以下安装计划？\n\n{plan}",
        "confirm_remove": "卸载 {name}？不再使用的依赖也会一并卸载。",
        "settings_saved": "设置已保存",
        "install_done": "已安装 {name}",
        "remove_done": "已卸载：{names}",
        "up_to_date": "所有主动安装的模组均为最新版",
        "updates_done": "已更新 {count} 个模组",
        "game_path_required": "请先在设置中填写有效的 Sprocket 游戏路径。",
        "available": "可用版本 {version}",
        "resolving": "正在解析 {name}...",
        "package_count": "{count} 个软件包",
        "installed_count": "{count} 个受管软件包",
        "registry_connected": "REGISTRY 已连接",
        "registry_loading": "REGISTRY 同步中",
        "category_all": "全部分类",
        "category_gameplay": "玩法",
        "category_utility": "工具",
        "category_library": "依赖库",
        "category_visual": "视觉",
        "category_audio": "音频",
        "category_other": "其他",
        "package_id": "包 ID",
        "release_state": "RELEASE 状态",
        "package_info": "软件包信息",
        "dependency_info": "依赖要求",
        "requested": "主动安装",
        "dependency": "依赖安装",
        "not_set": "未设置",
        "version_label": "版本 {version}",
        "dialog_confirm": "确认操作",
        "dialog_warning": "注意",
        "dialog_error": "错误",
        "confirm": "确认",
        "cancel": "取消",
        "close": "关闭",
        "manager_update_title": "模组管理器有新版本",
        "manager_update_eyebrow": "发现更新",
        "manager_update_message": "新版本 {latest} 已发布，当前版本为 {current}。",
        "view_release": "查看 Release",
        "later": "稍后",
    },
}


def load_catalog(
    service: ModManagerService,
    source: str | Path,
    *,
    refresh: bool,
) -> tuple[ModManagerService, dict[str, ReleaseInfo | None]]:
    registry = service.load_registry(source, refresh=refresh)

    def load_latest(package: RegistryPackage) -> tuple[str, ReleaseInfo | None]:
        releases = service.github.releases(package, refresh=refresh)
        usable = [
            release
            for release in releases
            if service.github.install_assets(package, release)
        ]
        return package.id, usable[0] if usable else None

    packages = tuple(registry.packages)
    if not packages:
        return service, {}
    with ThreadPoolExecutor(max_workers=min(4, len(packages))) as executor:
        latest = dict(executor.map(load_latest, packages))
    return service, latest


def _bind_click_tree(widget: object, command: Callable[[], None]) -> None:
    widget.bind("<Button-1>", lambda _event: command(), add="+")  # type: ignore[attr-defined]
    for child in widget.winfo_children():  # type: ignore[attr-defined]
        _bind_click_tree(child, command)


class ModManagerApp(ctk.CTk):
    def __init__(self, version: str):
        ctk.set_appearance_mode("dark")
        super().__init__()
        self.version = version
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        configured_language = self.config.get("language", "auto")
        self.language_mode = configured_language if configured_language in {"auto", "en", "zh"} else "auto"
        self.language = detect_language() if self.language_mode == "auto" else self.language_mode
        self.service = ModManagerService(self.config_store.app_dir)
        self.latest: dict[str, ReleaseInfo | None] = {}
        self.selected: RegistryPackage | None = None
        self.busy = False
        self.current_tab = "browse"
        self.category = "all"
        self.status_key: str | None = None
        self.status_values: dict[str, object] = {}
        self.status = ctk.StringVar(value="")

        self.title(f"{self.tr('title')} {version}")
        self.geometry("1180x760")
        self.minsize(940, 620)
        self.configure(fg_color=COLORS["canvas"])
        self.grid_columnconfigure(0, minsize=210)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_interface()
        self.after(100, lambda: self.refresh(force=False))
        self.after(1000, self.check_for_updates)

    def tr(self, key: str, **values: object) -> str:
        text = TEXT.get(self.language, TEXT["en"]).get(key, TEXT["en"].get(key, key))
        return text.format(**values) if values else text

    def set_status(self, key: str, **values: object) -> None:
        self.status_key = key
        self.status_values = values
        self.status.set(self.tr(key, **values))

    def set_raw_status(self, value: str) -> None:
        self.status_key = None
        self.status_values = {}
        self.status.set(value)

    def _build_interface(self, search_value: str = "") -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self.title(f"{self.tr('title')} {self.version}")
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=(22, 14))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        self._search_value = search_value
        self._ensure_page(self.current_tab)
        self._build_statusbar()

        self.show_page(self.current_tab)
        if self.service.registry:
            self.populate_packages()
            self.populate_installed()
            if self.selected and "browse" in self.pages:
                try:
                    self.select_package(self.service.registry.get(self.selected.id))
                except ModManagerError:
                    self.selected = None
        self._set_busy(self.busy)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=210,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
            border_width=1,
            border_color=COLORS["line_soft"],
        )
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0)
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))
        brand.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(brand, width=4, height=38, corner_radius=0, fg_color=COLORS["accent"]).grid(
            row=0, column=0, rowspan=2, sticky="ns", padx=(0, 11)
        )
        ctk.CTkLabel(
            brand,
            text="SPROCKET",
            anchor="w",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            brand,
            text="MOD MANAGER",
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11),
        ).grid(row=1, column=1, sticky="ew")

        navigation = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0)
        navigation.grid(row=1, column=0, sticky="ew", padx=10)
        navigation.grid_columnconfigure(0, weight=1)
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for row, (name, key) in enumerate(
            (("browse", "browse"), ("installed", "installed"), ("settings", "settings"))
        ):
            button = ctk.CTkButton(
                navigation,
                text=self.tr(key),
                height=42,
                anchor="w",
                corner_radius=3,
                border_spacing=13,
                fg_color="transparent",
                hover_color=COLORS["surface_hover"],
                text_color=COLORS["text_soft"],
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda page=name: self.show_page(page),
            )
            button.grid(row=row, column=0, sticky="ew", pady=2)
            self.nav_buttons[name] = button

        footer = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=0)
        footer.grid(row=3, column=0, sticky="sew", padx=18, pady=18)
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text=self.tr("language").upper(),
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 7))
        self.language_options = {
            "auto": self.tr("language_auto"),
            "zh": "中文",
            "en": "EN",
        }
        self.language_choice = ctk.StringVar(
            value=self.language_options[self.language_mode]
        )
        ctk.CTkSegmentedButton(
            footer,
            values=list(self.language_options.values()),
            variable=self.language_choice,
            height=32,
            corner_radius=3,
            fg_color=COLORS["canvas"],
            selected_color=COLORS["surface_hover"],
            selected_hover_color=COLORS["surface_hover"],
            unselected_color=COLORS["canvas"],
            unselected_hover_color=COLORS["surface_high"],
            text_color=COLORS["text_soft"],
            command=self.change_language,
        ).grid(row=1, column=0, sticky="ew")
        ctk.CTkFrame(footer, height=1, fg_color=COLORS["line"], corner_radius=0).grid(
            row=2, column=0, sticky="ew", pady=16
        )
        ctk.CTkLabel(
            footer,
            text=self.tr("brand_registry"),
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11),
        ).grid(row=3, column=0, sticky="ew")
        ctk.CTkLabel(
            footer,
            text=f"v{self.version}",
            anchor="w",
            text_color="#5F6863",
            font=ctk.CTkFont(family="Cascadia Mono", size=11),
        ).grid(row=4, column=0, sticky="ew", pady=(3, 0))

    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(
            self,
            height=38,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
            border_width=1,
            border_color=COLORS["line_soft"],
        )
        bar.grid(row=1, column=1, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)
        self.status_indicator = ctk.CTkLabel(
            bar,
            text="●",
            width=20,
            text_color=COLORS["green"] if not self.busy else COLORS["accent"],
            font=ctk.CTkFont(size=11),
        )
        self.status_indicator.grid(row=0, column=0, padx=(15, 2))
        ctk.CTkLabel(
            bar,
            textvariable=self.status,
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1, sticky="ew")
        self.connection_label = ctk.CTkLabel(
            bar,
            text=self.tr("registry_loading") if self.busy else self.tr("registry_connected"),
            anchor="e",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
        )
        self.connection_label.grid(row=0, column=2, padx=16)

    def _new_page(self, name: str) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent", corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages[name] = page
        return page

    def _ensure_page(self, name: str) -> None:
        if name in self.pages:
            return
        if name == "browse":
            self._build_browse(self._search_value)
        elif name == "installed":
            self._build_installed()
        elif name == "settings":
            self._build_settings()

    def _page_header(self, page: ctk.CTkFrame, title: str, subtitle: str) -> ctk.CTkFrame:
        header = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=title,
            anchor="w",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header,
            text=subtitle,
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="ew", pady=(3, 0))
        return header

    def _build_browse(self, search_value: str) -> None:
        page = self._new_page("browse")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        header = self._page_header(page, self.tr("catalog_title"), self.tr("catalog_subtitle"))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.catalog_count = ctk.CTkLabel(
            header,
            text=self.tr("package_count", count=0),
            text_color=COLORS["green"],
            fg_color=COLORS["green_quiet"],
            corner_radius=3,
            padx=10,
            height=28,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.catalog_count.grid(row=0, column=1, rowspan=2, padx=(16, 0))

        toolbar = ctk.CTkFrame(
            page,
            height=58,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
        )
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        toolbar.grid_columnconfigure(0, weight=1)
        self.search = ctk.StringVar(value=search_value)
        self.search.trace_add("write", lambda *_: self.populate_packages())
        self.search_entry = ctk.CTkEntry(
            toolbar,
            textvariable=self.search,
            placeholder_text=self.tr("search"),
            height=36,
            corner_radius=3,
            border_color=COLORS["line"],
            fg_color=COLORS["canvas"],
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(10, 7), pady=10)

        category_labels = [self.tr(CATEGORY_KEYS[key]) for key in CATEGORY_KEYS]
        self.category_value = ctk.StringVar(value=self.tr(CATEGORY_KEYS[self.category]))
        self.category_menu = ctk.CTkOptionMenu(
            toolbar,
            values=category_labels,
            variable=self.category_value,
            width=145,
            height=36,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            button_color=COLORS["surface_hover"],
            button_hover_color=COLORS["line"],
            dropdown_fg_color=COLORS["surface_high"],
            dropdown_hover_color=COLORS["accent_quiet"],
            command=self.change_category,
        )
        self.category_menu.grid(row=0, column=1, padx=7, pady=10)

        self.refresh_button = ctk.CTkButton(
            toolbar,
            text=self.tr("refresh"),
            width=88,
            height=36,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=lambda: self.refresh(force=True),
        )
        self.refresh_button.grid(row=0, column=2, padx=(7, 10), pady=10)

        workspace = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=6, minsize=430)
        workspace.grid_columnconfigure(1, weight=5, minsize=345)
        workspace.grid_rowconfigure(0, weight=1)

        self.package_list = ctk.CTkScrollableFrame(
            workspace,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
            scrollbar_button_color=COLORS["surface_hover"],
            scrollbar_button_hover_color=COLORS["line"],
        )
        self.package_list.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.package_list.grid_columnconfigure(0, weight=1)
        self.package_rows: dict[str, ctk.CTkFrame] = {}

        detail = ctk.CTkFrame(
            workspace,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
        )
        detail.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(6, weight=1)

        self.detail_kicker = ctk.CTkLabel(
            detail,
            text=self.tr("release_state"),
            anchor="w",
            text_color=COLORS["accent"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
        )
        self.detail_kicker.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 3))
        self.detail_title = ctk.CTkLabel(
            detail,
            text=self.tr("select_mod"),
            anchor="w",
            justify="left",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=19, weight="bold"),
        )
        self.detail_title.grid(row=1, column=0, sticky="ew", padx=18)
        self.detail_state = ctk.CTkLabel(
            detail,
            text=self.tr("select_mod_hint"),
            anchor="w",
            justify="left",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        )
        self.detail_state.grid(row=2, column=0, sticky="ew", padx=18, pady=(3, 14))
        ctk.CTkFrame(detail, height=1, fg_color=COLORS["line"], corner_radius=0).grid(
            row=3, column=0, sticky="ew"
        )
        self.detail_description = ctk.CTkLabel(
            detail,
            text="",
            anchor="nw",
            justify="left",
            wraplength=360,
            text_color=COLORS["text_soft"],
            font=ctk.CTkFont(size=13),
        )
        self.detail_description.grid(row=4, column=0, sticky="ew", padx=18, pady=(15, 11))

        info = ctk.CTkFrame(detail, fg_color=COLORS["canvas"], corner_radius=3)
        info.grid(row=5, column=0, sticky="ew", padx=18)
        info.grid_columnconfigure(1, weight=1)
        self.detail_fields: dict[str, ctk.CTkLabel] = {}
        for row, key in enumerate(("authors", "license", "category", "assets")):
            ctk.CTkLabel(
                info,
                text=self.tr(key).upper(),
                width=82,
                anchor="w",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(family="Cascadia Mono", size=10, weight="bold"),
            ).grid(row=row, column=0, sticky="w", padx=(11, 7), pady=5)
            value = ctk.CTkLabel(
                info,
                text="-",
                anchor="w",
                justify="left",
                wraplength=245,
                text_color=COLORS["text_soft"],
                font=ctk.CTkFont(size=12),
            )
            value.grid(row=row, column=1, sticky="ew", padx=(0, 11), pady=5)
            self.detail_fields[key] = value

        dependency_area = ctk.CTkFrame(detail, fg_color="transparent", corner_radius=0)
        dependency_area.grid(row=6, column=0, sticky="new", padx=18, pady=14)
        ctk.CTkLabel(
            dependency_area,
            text=self.tr("dependency_info"),
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=10, weight="bold"),
        ).pack(fill="x")
        self.detail_dependencies = ctk.CTkLabel(
            dependency_area,
            text="-",
            anchor="nw",
            justify="left",
            wraplength=350,
            text_color=COLORS["text_soft"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11),
        )
        self.detail_dependencies.pack(fill="x", pady=(6, 0))

        actions = ctk.CTkFrame(
            detail,
            fg_color=COLORS["sidebar"],
            corner_radius=0,
            border_width=1,
            border_color=COLORS["line_soft"],
        )
        actions.grid(row=7, column=0, sticky="ew")
        actions.grid_columnconfigure(2, weight=1)
        self.install_button = ctk.CTkButton(
            actions,
            text=self.tr("install"),
            width=92,
            height=36,
            corner_radius=3,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.begin_install,
            state="disabled",
        )
        self.install_button.grid(row=0, column=0, padx=(12, 5), pady=12)
        self.remove_button = ctk.CTkButton(
            actions,
            text=self.tr("remove"),
            width=80,
            height=36,
            corner_radius=3,
            fg_color="transparent",
            hover_color=COLORS["danger"],
            border_width=1,
            border_color=COLORS["danger"],
            command=self.begin_remove,
            state="disabled",
        )
        self.remove_button.grid(row=0, column=1, padx=5, pady=12)
        self.repo_button = ctk.CTkButton(
            actions,
            text=self.tr("repository"),
            width=100,
            height=36,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=self.open_repository,
            state="disabled",
        )
        self.repo_button.grid(row=0, column=3, padx=(5, 12), pady=12)

    def _build_installed(self) -> None:
        page = self._new_page("installed")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        header = self._page_header(page, self.tr("installed_title"), self.tr("installed_subtitle"))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.update_all_button = ctk.CTkButton(
            header,
            text=self.tr("update_all"),
            width=110,
            height=36,
            corner_radius=3,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.update_all,
        )
        self.update_all_button.grid(row=0, column=1, rowspan=2, padx=(16, 0))

        summary = ctk.CTkFrame(
            page,
            height=46,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
        )
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        summary.grid_propagate(False)
        self.installed_count = ctk.CTkLabel(
            summary,
            text=self.tr("installed_count", count=0),
            anchor="w",
            text_color=COLORS["text_soft"],
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.installed_count.pack(fill="x", padx=14, pady=12)

        self.installed_list = ctk.CTkScrollableFrame(
            page,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
            scrollbar_button_color=COLORS["surface_hover"],
            scrollbar_button_hover_color=COLORS["line"],
        )
        self.installed_list.grid(row=2, column=0, sticky="nsew")
        self.installed_list.grid_columnconfigure(0, weight=1)

    def _build_settings(self) -> None:
        page = self._new_page("settings")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        header = self._page_header(page, self.tr("settings_title"), self.tr("settings_subtitle"))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))

        area = ctk.CTkFrame(
            page,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
        )
        area.grid(row=1, column=0, sticky="nsew")
        area.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(area, width=720, fg_color="transparent", corner_radius=0)
        form.grid(row=0, column=0, sticky="new", padx=28, pady=26)
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            form,
            text=self.tr("game_path").upper(),
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 7))
        path_row = ctk.CTkFrame(form, fg_color="transparent", corner_radius=0)
        path_row.grid(row=1, column=0, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)
        configured_game_path = self.config.get("game_path", "")
        if not isinstance(configured_game_path, str):
            configured_game_path = ""
        self.game_path_entry = ctk.CTkEntry(
            path_row,
            placeholder_text=detect_game_path() or self.tr("steam_not_found"),
            height=38,
            corner_radius=3,
            border_color=COLORS["line"],
            fg_color=COLORS["canvas"],
        )
        self.game_path_entry.grid(row=0, column=0, sticky="ew")
        if configured_game_path.strip():
            self.game_path_entry.insert(0, configured_game_path.strip())
        ctk.CTkButton(
            path_row,
            text=self.tr("browse_path"),
            width=88,
            height=38,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=self.select_game_path,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkFrame(form, height=1, fg_color=COLORS["line"], corner_radius=0).grid(
            row=2, column=0, sticky="ew", pady=22
        )
        ctk.CTkLabel(
            form,
            text=self.tr("index_url").upper(),
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
        ).grid(row=3, column=0, sticky="ew", pady=(0, 7))
        configured_index_url = self.config.get("index_url", "")
        if not isinstance(configured_index_url, str):
            configured_index_url = ""
        self.index_source_entry = ctk.CTkEntry(
            form,
            placeholder_text=DEFAULT_INDEX_URL,
            height=38,
            corner_radius=3,
            border_color=COLORS["line"],
            fg_color=COLORS["canvas"],
        )
        self.index_source_entry.grid(row=4, column=0, sticky="ew")
        if configured_index_url.strip():
            self.index_source_entry.insert(0, configured_index_url.strip())

        ctk.CTkButton(
            form,
            text=self.tr("save"),
            width=120,
            height=38,
            corner_radius=3,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.save_settings,
        ).grid(row=5, column=0, sticky="w", pady=(28, 0))

    def change_language(self, value: str) -> None:
        mode_by_value = {
            label: mode for mode, label in self.language_options.items()
        }
        language_mode = mode_by_value.get(value)
        if not language_mode:
            return
        language = detect_language() if language_mode == "auto" else language_mode
        if language_mode == self.language_mode and language == self.language:
            return
        search_value = self.search.get() if "browse" in self.pages else self._search_value
        self.language_mode = language_mode
        self.language = language
        self.config["language"] = language_mode
        self.config_store.save(self.config)
        if self.status_key:
            self.status.set(self.tr(self.status_key, **self.status_values))
        self.after_idle(lambda: self._build_interface(search_value))

    def change_category(self, value: str) -> None:
        for key, translation_key in CATEGORY_KEYS.items():
            if value == self.tr(translation_key):
                self.category = key
                break
        self.populate_packages()

    def show_page(self, name: str) -> None:
        if name not in self.nav_buttons:
            return
        if name == "settings":
            self.config = self.config_store.load()
            existing = self.pages.pop("settings", None)
            if existing is not None:
                existing.destroy()
        created = name not in self.pages
        self._ensure_page(name)
        self.current_tab = name
        self.pages[name].tkraise()
        for page, button in self.nav_buttons.items():
            selected = page == name
            button.configure(
                fg_color=COLORS["accent_quiet"] if selected else "transparent",
                hover_color=COLORS["accent_quiet"] if selected else COLORS["surface_hover"],
                text_color=COLORS["text"] if selected else COLORS["text_soft"],
                border_width=1 if selected else 0,
                border_color="#513027",
            )
        if name == "browse" and created:
            self.populate_packages()
            if self.selected and self.service.registry:
                try:
                    self.select_package(self.service.registry.get(self.selected.id))
                except ModManagerError:
                    self.selected = None
        if name == "installed":
            self.populate_installed()
        if created:
            self._set_busy(self.busy)

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        if "browse" in self.pages:
            self.refresh_button.configure(state=state)
        if "installed" in self.pages:
            self.update_all_button.configure(state=state)
        if hasattr(self, "status_indicator"):
            self.status_indicator.configure(text_color=COLORS["accent"] if value else COLORS["green"])
        if hasattr(self, "connection_label"):
            self.connection_label.configure(
                text=self.tr("registry_loading") if value else self.tr("registry_connected"),
                text_color=COLORS["accent"] if value else COLORS["muted"],
            )
        if "browse" not in self.pages:
            return
        if value:
            self.install_button.configure(state="disabled")
            self.remove_button.configure(state="disabled")
        elif self.selected:
            self.select_package(self.selected)

    def _background(self, operation: Callable[[], object], success: Callable[[object], None]) -> None:
        if self.busy:
            return
        self._set_busy(True)

        def run() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.after(0, lambda error=exc: self._task_failed(error))
                return
            self.after(0, lambda: self._task_succeeded(result, success))

        threading.Thread(target=run, daemon=True).start()

    def _task_failed(self, error: Exception) -> None:
        self._set_busy(False)
        self.set_raw_status(str(error))
        show_message(
            self,
            title=self.tr("title"),
            eyebrow=self.tr("dialog_error"),
            message=str(error),
            close_text=self.tr("close"),
            palette=COLORS,
            danger=True,
        )

    def _task_succeeded(self, result: object, success: Callable[[object], None]) -> None:
        self._set_busy(False)
        success(result)

    def check_for_updates(self) -> None:
        github = self.service.github
        current_version = self.version

        def run() -> None:
            try:
                release = github.latest_repository_release(MANAGER_REPOSITORY)
                current = Version.parse(current_version)
            except (DownloadError, ValueError):
                return
            if release.version <= current:
                return
            try:
                self.after(0, lambda update=release: self._offer_update(update))
            except RuntimeError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _offer_update(self, release: RepositoryRelease) -> None:
        if ask_confirmation(
            self,
            title=self.tr("manager_update_title"),
            eyebrow=self.tr("manager_update_eyebrow"),
            message=self.tr(
                "manager_update_message",
                latest=str(release.version),
                current=self.version,
            ),
            confirm_text=self.tr("view_release"),
            cancel_text=self.tr("later"),
            palette=COLORS,
        ):
            webbrowser.open(release.page_url)

    def _source(self) -> str | Path:
        source = effective_index_url(self.config)
        path = Path(source).expanduser()
        return path if path.is_file() else source

    def refresh(self, *, force: bool = True) -> None:
        self.set_status("loading")

        def operation() -> tuple[ModManagerService, dict[str, ReleaseInfo | None]]:
            service = ModManagerService(self.config_store.app_dir)
            return load_catalog(service, self._source(), refresh=force)

        def success(result: object) -> None:
            service, latest = result  # type: ignore[misc]
            self.service = service
            self.latest = latest
            count = len(service.registry.packages) if service.registry else 0
            self.set_status("ready", count=count)
            self.populate_packages()
            self.populate_installed()
            if self.selected and service.registry:
                try:
                    self.select_package(service.registry.get(self.selected.id))
                except ModManagerError:
                    self.selected = None

        self._background(operation, success)

    def _installed(self) -> dict[str, dict]:
        value = effective_game_path(self.config)
        if not value:
            return {}
        return self.service.installed(Path(value))

    def populate_packages(self) -> None:
        if "browse" not in self.pages:
            return
        for widget in self.package_list.winfo_children():
            widget.destroy()
        self.package_rows = {}
        registry = self.service.registry
        if not registry:
            self.catalog_count.configure(text=self.tr("package_count", count=0))
            return
        keyword = self.search.get().strip().casefold()
        installed = self._installed()
        visible: list[RegistryPackage] = []
        for package in registry.packages:
            if self.category != "all" and package.category != self.category:
                continue
            haystack = " ".join(
                [
                    package.id,
                    package.name,
                    package.repository,
                    " ".join(package.authors),
                    package.label(self.language),
                    " ".join(package.tags),
                ]
            ).casefold()
            if keyword and keyword not in haystack:
                continue
            visible.append(package)

        self.catalog_count.configure(text=self.tr("package_count", count=len(visible)))
        for row_index, package in enumerate(visible):
            selected = bool(self.selected and self.selected.id == package.id)
            row = ctk.CTkFrame(
                self.package_list,
                height=76,
                fg_color=COLORS["accent_quiet"] if selected else COLORS["surface_high"],
                border_width=1,
                border_color="#513027" if selected else COLORS["line_soft"],
                corner_radius=3,
            )
            row.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
            row.grid_columnconfigure(0, weight=1)
            title_row = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
            title_row.grid(row=0, column=0, sticky="ew", padx=11, pady=(9, 1))
            title_row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                title_row,
                text=package.label(self.language),
                anchor="w",
                text_color=COLORS["text"],
                font=ctk.CTkFont(size=12, weight="bold"),
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                title_row,
                text=self.tr(CATEGORY_KEYS.get(package.category, "category_other")),
                width=58,
                height=20,
                text_color=COLORS["text_soft"],
                fg_color=COLORS["surface_hover"],
                corner_radius=2,
                font=ctk.CTkFont(size=11),
            ).grid(row=0, column=1, padx=(7, 0))

            release = self.latest.get(package.id)
            available = str(release.version) if release else "-"
            install_state = installed.get(package.id)
            state_text = ""
            if install_state:
                key = "installed_state" if install_state.get("requested") else "dependency_state"
                state_text = "  |  " + self.tr(key, version=install_state.get("version", "-"))
            ctk.CTkLabel(
                row,
                text=f"{package.repository}  |  v{available}{state_text}",
                anchor="w",
                text_color="#959D99",
                font=ctk.CTkFont(family="Cascadia Mono", size=11),
            ).grid(row=1, column=0, sticky="ew", padx=11, pady=(1, 9))
            _bind_click_tree(
                row,
                lambda item=package: self.select_package(item),
            )
            self.package_rows[package.id] = row

        if not visible:
            ctk.CTkLabel(
                self.package_list,
                text=self.tr("no_mods"),
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, pady=32)

    def select_package(self, package: RegistryPackage) -> None:
        self.selected = package
        release = self.latest.get(package.id)
        installed = self._installed().get(package.id)
        self.detail_kicker.configure(text=self.tr("release_state"))
        self.detail_title.configure(text=package.label(self.language))
        if installed:
            key = "installed_state" if installed.get("requested") else "dependency_state"
            state = self.tr(key, version=installed.get("version", "-"))
            state_color = COLORS["green"]
        elif release:
            state = self.tr("available", version=release.version)
            state_color = COLORS["green"]
        else:
            state = self.tr("unavailable")
            state_color = COLORS["muted"]
        self.detail_state.configure(text=state, text_color=state_color)
        self.detail_description.configure(
            text=package.description_text(self.language)
        )
        assets = (
            ", ".join(asset.name for asset in self.service.github.install_assets(package, release))
            if release
            else "-"
        )
        self.detail_fields["authors"].configure(text=", ".join(package.authors))
        self.detail_fields["license"].configure(text=package.license)
        self.detail_fields["category"].configure(
            text=self.tr(CATEGORY_KEYS.get(package.category, "category_other"))
        )
        self.detail_fields["assets"].configure(text=assets)
        dependencies = [f"{item['id']}  {item['version']}" for item in package.dependencies]
        self.detail_dependencies.configure(text="\n".join(dependencies) if dependencies else self.tr("not_set"))
        self.repo_button.configure(state="normal")
        self.remove_button.configure(state="normal" if installed and not self.busy else "disabled")
        if release and not self.busy:
            action = "update" if installed and installed.get("version") != str(release.version) else "install"
            disabled = bool(installed and installed.get("version") == str(release.version))
            self.install_button.configure(text=self.tr(action), state="disabled" if disabled else "normal")
        else:
            self.install_button.configure(text=self.tr("install"), state="disabled")
        for package_id, row in self.package_rows.items():
            selected = package_id == package.id
            row.configure(
                fg_color=COLORS["accent_quiet"] if selected else COLORS["surface_high"],
                border_color="#513027" if selected else COLORS["line_soft"],
            )

    def _valid_game_path(self) -> Path | None:
        value = effective_game_path(self.config)
        path = Path(value).expanduser() if value else None
        if not path or not (path / "Sprocket.exe").is_file():
            show_message(
                self,
                title=self.tr("title"),
                eyebrow=self.tr("dialog_warning"),
                message=self.tr("game_path_required"),
                close_text=self.tr("close"),
                palette=COLORS,
            )
            self.show_page("settings")
            return None
        return path

    def begin_install(self) -> None:
        package = self.selected
        game_path = self._valid_game_path()
        if not package or not game_path:
            return
        self.set_status("resolving", name=package.label(self.language))

        def resolved(result: object) -> None:
            plan = result
            assert isinstance(plan, ResolutionPlan)
            lines = []
            for item in plan.packages:
                assets = ", ".join(
                    asset.name for asset in self.service.github.install_assets(item.package, item.release)
                )
                lines.append(f"{item.package.label(self.language)} {item.release.tag}\n  {assets}")
            if not ask_confirmation(
                self,
                title=self.tr("title"),
                eyebrow=self.tr("dialog_confirm"),
                message=self.tr("confirm_install", plan="\n".join(lines)),
                confirm_text=self.tr("confirm"),
                cancel_text=self.tr("cancel"),
                palette=COLORS,
            ):
                return
            self._install(package, game_path)

        self._background(lambda: self.service.resolve(package.id), resolved)

    def _install(self, package: RegistryPackage, game_path: Path) -> None:
        def progress(message: str) -> None:
            self.after(0, lambda: self.set_raw_status(message))

        def success(result: object) -> None:
            _, warnings = result  # type: ignore[misc]
            message = self.tr("install_done", name=package.label(self.language))
            if warnings:
                message += " | " + "; ".join(warnings)
            self.set_raw_status(message)
            self.populate_packages()
            self.populate_installed()
            self.select_package(package)

        self._background(lambda: self.service.install(package.id, game_path, progress=progress), success)

    def begin_remove(self) -> None:
        package = self.selected
        game_path = self._valid_game_path()
        if not package or not game_path:
            return
        if not ask_confirmation(
            self,
            title=self.tr("title"),
            eyebrow=self.tr("dialog_confirm"),
            message=self.tr("confirm_remove", name=package.label(self.language)),
            confirm_text=self.tr("remove"),
            cancel_text=self.tr("cancel"),
            palette=COLORS,
            destructive=True,
        ):
            return

        def success(result: object) -> None:
            removed, warnings = result  # type: ignore[misc]
            message = self.tr("remove_done", names=", ".join(removed))
            if warnings:
                message += " | " + "; ".join(warnings)
            self.set_raw_status(message)
            self.populate_packages()
            self.populate_installed()
            self.select_package(package)

        self._background(lambda: self.service.remove(package.id, game_path), success)

    def populate_installed(self) -> None:
        if "installed" not in self.pages:
            return
        for widget in self.installed_list.winfo_children():
            widget.destroy()
        installed = self._installed()
        self.installed_count.configure(text=self.tr("installed_count", count=len(installed)))
        if not installed:
            ctk.CTkLabel(
                self.installed_list,
                text=self.tr("no_installed"),
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, pady=36)
            return
        for row_index, (package_id, info) in enumerate(sorted(installed.items())):
            row = ctk.CTkFrame(
                self.installed_list,
                height=72,
                corner_radius=3,
                fg_color=COLORS["surface_high"],
                border_width=1,
                border_color=COLORS["line_soft"],
            )
            row.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row,
                text=info.get("name", package_id),
                anchor="w",
                text_color=COLORS["text"],
                font=ctk.CTkFont(size=12, weight="bold"),
            ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 1))
            key = "requested" if info.get("requested") else "dependency"
            ctk.CTkLabel(
                row,
                text=f"{package_id}  |  {self.tr('version_label', version=info.get('version', '-'))}  |  {self.tr(key)}",
                anchor="w",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(family="Cascadia Mono", size=11),
            ).grid(row=1, column=0, sticky="ew", padx=12, pady=(1, 10))
            package = self.service.registry.get(package_id) if self.service.registry else None
            if package:
                ctk.CTkButton(
                    row,
                    text=self.tr("remove"),
                    width=78,
                    height=32,
                    corner_radius=3,
                    fg_color="transparent",
                    hover_color=COLORS["danger"],
                    border_width=1,
                    border_color=COLORS["danger"],
                    command=lambda item=package: (self.select_package(item), self.begin_remove()),
                ).grid(row=0, column=1, rowspan=2, padx=12, pady=12)

    def update_all(self) -> None:
        game_path = self._valid_game_path()
        if not game_path:
            return

        def operation() -> int:
            installed = self.service.installed(game_path)
            changed = 0
            for package_id, info in installed.items():
                if not info.get("requested"):
                    continue
                plan = self.service.resolve(package_id)
                latest = plan.by_id()[package_id].release.version
                if info.get("version") == str(latest):
                    continue
                self.service.install(
                    package_id,
                    game_path,
                    progress=lambda message: self.after(
                        0, lambda value=message: self.set_raw_status(value)
                    ),
                )
                changed += 1
            return changed

        def success(result: object) -> None:
            changed = int(result)
            self.set_status("updates_done", count=changed) if changed else self.set_status("up_to_date")
            self.populate_packages()
            self.populate_installed()

        self._background(operation, success)

    def open_repository(self) -> None:
        if self.selected:
            webbrowser.open(f"https://github.com/{self.selected.repository}")

    def select_game_path(self) -> None:
        selected = filedialog.askdirectory(title=self.tr("game_path"))
        if selected:
            self.game_path_entry.delete(0, "end")
            self.game_path_entry.insert(0, selected)

    def save_settings(self) -> None:
        self.config["game_path"] = self.game_path_entry.get().strip()
        self.config["index_url"] = self.index_source_entry.get().strip()
        self.config["language"] = self.language_mode
        self.config_store.save(self.config)
        self.set_status("settings_saved")
        self.after(50, lambda: self.refresh(force=True))


def run_gui(version: str) -> None:
    ModManagerApp(version).mainloop()
