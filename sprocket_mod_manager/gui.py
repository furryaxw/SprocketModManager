from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog

from .catalog import load_catalog
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
from .install_queue import (
    ACTIVE_STATES,
    CANCELED,
    COMPLETED,
    FAILED,
    INSTALLING,
    WAITING,
    InstallQueue,
    InstallQueueEntry,
)
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
    "button_accent": "#B6402D",
    "button_accent_hover": "#BE4430",
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
    "translation": "category_translation",
    "other": "category_other",
}

MANAGER_REPOSITORY = "furryaxw/sprocket-mods"
MANAGER_REPOSITORY_URL = f"https://github.com/{MANAGER_REPOSITORY}"
REGISTRY_WEBSITE_URL = "https://sprocketmods.furryaxw.top"

TEXT = {
    "en": {
        "title": "Sprocket Mod Manager",
        "brand_registry": "OPEN-SOURCE REGISTRY",
        "browse": "Catalog",
        "installed": "Installed",
        "downloads": "Download queue",
        "settings": "Settings",
        "about": "About",
        "catalog_title": "Mod catalog",
        "catalog_subtitle": "Packages sourced directly from GitHub Releases",
        "installed_title": "Managed installation",
        "installed_subtitle": "Packages tracked for the selected Sprocket directory",
        "downloads_title": "Download queue",
        "downloads_subtitle": "Queued installs run sequentially without blocking the interface",
        "settings_title": "Application settings",
        "settings_subtitle": "Game location and registry source",
        "about_title": "About the manager",
        "about_subtitle": "Version, project links and distribution information",
        "current_version": "Current version",
        "latest_version": "Latest version",
        "source_repository": "Source repository",
        "registry_website": "Registry website",
        "open_registry": "Open Registry",
        "check_updates": "Check updates",
        "checking_updates": "Checking...",
        "view_update": "View update",
        "manager_up_to_date": "Up to date",
        "update_unavailable": "Unavailable",
        "search": "Search name, author or tag",
        "refresh": "Refresh",
        "install": "Install",
        "update": "Update",
        "remove": "Remove",
        "update_all": "Update all",
        "batch_install": "Install selected ({count})",
        "clear_finished": "Clear finished",
        "queue_empty": "No queued installs",
        "queue_count": "{count} queue items",
        "queue_waiting": "Waiting",
        "queue_installing": "Installing",
        "queue_completed": "Completed",
        "queue_failed": "Failed",
        "queue_canceled": "Canceled",
        "cancel_queue_item": "Cancel",
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
        "confirm_batch_install": "Queue these install plans?\n\n{plan}",
        "confirm_remove": "Remove {name}? Unused dependency packages will also be removed.",
        "settings_saved": "Settings saved",
        "install_done": "Installed {name}",
        "installs_queued": "Added {count} package(s) to the download queue",
        "queue_item_failed": "Install failed for {name}: {message}",
        "loading_releases": "Registry loaded - checking {count} GitHub Releases...",
        "remove_done": "Removed: {names}",
        "all_mods_up_to_date": "No updates available",
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
        "category_translation": "Translations",
        "category_other": "Other",
        "package_id": "PACKAGE ID",
        "release_state": "RELEASE STATE",
        "package_info": "PACKAGE INFORMATION",
        "dependency_info": "DEPENDENCY REQUIREMENTS",
        "requested": "User-installed",
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
        "downloads": "下载队列",
        "settings": "设置",
        "about": "关于",
        "catalog_title": "模组目录",
        "catalog_subtitle": "软件包直接取自 GitHub Releases",
        "installed_title": "安装管理",
        "installed_subtitle": "当前 Sprocket 目录中由管理器追踪的软件包",
        "downloads_title": "下载队列",
        "downloads_subtitle": "安装任务按顺序执行，不阻塞界面操作",
        "settings_title": "应用设置",
        "settings_subtitle": "游戏位置与 Registry 来源",
        "about_title": "关于模组管理器",
        "about_subtitle": "版本、项目链接与分发信息",
        "current_version": "当前版本",
        "latest_version": "最新版本",
        "source_repository": "源码仓库",
        "registry_website": "Registry 网站",
        "open_registry": "打开 Registry",
        "check_updates": "检查更新",
        "checking_updates": "正在检查…",
        "view_update": "获取更新",
        "manager_up_to_date": "已是最新",
        "update_unavailable": "暂不可用",
        "search": "搜索名称、作者或标签",
        "refresh": "刷新",
        "install": "安装",
        "update": "更新",
        "remove": "卸载",
        "update_all": "全部更新",
        "batch_install": "批量安装（{count}）",
        "clear_finished": "清除已完成",
        "queue_empty": "下载队列为空",
        "queue_count": "队列中有 {count} 项",
        "queue_waiting": "等待中",
        "queue_installing": "安装中",
        "queue_completed": "已完成",
        "queue_failed": "失败",
        "queue_canceled": "已取消",
        "cancel_queue_item": "取消",
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
        "confirm_batch_install": "确认将以下安装计划加入队列？\n\n{plan}",
        "confirm_remove": "卸载 {name}？不再使用的依赖也会一并卸载。",
        "settings_saved": "设置已保存",
        "install_done": "已安装 {name}",
        "installs_queued": "已将 {count} 个模组加入下载队列",
        "queue_item_failed": "{name} 安装失败：{message}",
        "loading_releases": "Registry 已加载，正在检查 {count} 个 GitHub Release…",
        "remove_done": "已卸载：{names}",
        "all_mods_up_to_date": "没有可用更新",
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
        "category_translation": "翻译",
        "category_other": "其他",
        "package_id": "包 ID",
        "release_state": "RELEASE 状态",
        "package_info": "软件包信息",
        "dependency_info": "依赖要求",
        "requested": "用户安装",
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


def _bind_click_tree(widget: object, command: Callable[[], None]) -> None:
    widget.bind("<Button-1>", lambda _event: command(), add="+")  # type: ignore[attr-defined]
    for child in widget.winfo_children():  # type: ignore[attr-defined]
        _bind_click_tree(child, command)


def _set_primary_button_enabled(button: object, enabled: bool) -> None:
    button.configure(  # type: ignore[attr-defined]
        state="normal" if enabled else "disabled",
        fg_color=COLORS["button_accent"] if enabled else COLORS["surface_high"],
        hover_color=COLORS["button_accent_hover"] if enabled else COLORS["surface_high"],
        text_color=COLORS["text"] if enabled else COLORS["muted"],
        text_color_disabled=COLORS["muted"],
    )


class FastScrollableFrame(tk.Frame):
    """Small Tk-backed scroller that avoids CustomTkinter's nested idle updates."""

    def __init__(self, master: object) -> None:
        super().__init__(
            master,
            background=COLORS["surface"],
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["line"],
            highlightthickness=1,
            borderwidth=0,
        )
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self,
            background=COLORS["surface"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.scrollbar = tk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            width=11,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            background=COLORS["surface_hover"],
            activebackground=COLORS["line"],
            troughcolor=COLORS["surface"],
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.content = tk.Frame(self.canvas, background=COLORS["surface"], borderwidth=0)
        self._window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._sync_scroll_region, add="+")
        self.canvas.bind("<Configure>", self._sync_content_width, add="+")
        self.canvas.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.content.bind("<MouseWheel>", self._on_mousewheel, add="+")

    def _sync_scroll_region(self, _event: object = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_content_width(self, event: object) -> None:
        self.canvas.itemconfigure(self._window_id, width=event.width)  # type: ignore[attr-defined]

    def _on_mousewheel(self, event: object) -> str:
        delta = int(getattr(event, "delta", 0))
        if delta:
            self.canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def bind_mousewheel_tree(self, widget: object) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")  # type: ignore[attr-defined]
        for child in widget.winfo_children():  # type: ignore[attr-defined]
            self.bind_mousewheel_tree(child)


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
        self.batch_selected: set[str] = set()
        self._background_busy = False
        self._queue_active = False
        self.busy = False
        self._catalog_generation = 0
        self._catalog_row_generation = 0
        self._search_after_id: str | None = None
        self._detected_game_path_loaded = False
        self._detected_game_path: str | None = None
        self._queue_snapshot: tuple[InstallQueueEntry, ...] = ()
        self._queue_states: dict[str, str] = {}
        self.install_queue = InstallQueue(self._run_queued_install)
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
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self._start_game_path_detection()
        self.after(75, self._poll_install_queue)
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
            (
                ("browse", "browse"),
                ("installed", "installed"),
                ("downloads", "downloads"),
                ("settings", "settings"),
                ("about", "about"),
            )
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
        elif name == "downloads":
            self._build_downloads()
        elif name == "settings":
            self._build_settings()
        elif name == "about":
            self._build_about()

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
        self.search.trace_add("write", lambda *_: self._schedule_package_filter())
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

        self.batch_install_button = ctk.CTkButton(
            toolbar,
            text=self.tr("batch_install", count=len(self.batch_selected)),
            width=120,
            height=36,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_high"],
            text_color=COLORS["muted"],
            text_color_disabled=COLORS["muted"],
            command=self.begin_batch_install,
            state="disabled",
        )
        self.batch_install_button.grid(row=0, column=2, padx=7, pady=10)

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
        self.refresh_button.grid(row=0, column=3, padx=(7, 10), pady=10)

        workspace = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=6, minsize=430)
        workspace.grid_columnconfigure(1, weight=5, minsize=345)
        workspace.grid_rowconfigure(0, weight=1)

        self.package_scroller = FastScrollableFrame(workspace)
        self.package_scroller.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.package_list = self.package_scroller.content
        self.package_list.grid_columnconfigure(0, weight=1)
        self.package_rows: dict[str, tk.Frame] = {}
        self.package_row_titles: dict[str, tk.Label] = {}
        self.package_row_categories: dict[str, tk.Label] = {}
        self.package_row_metadata: dict[str, tk.Label] = {}
        self.package_row_surfaces: dict[str, tuple[tk.Frame, tk.Label, tk.Label]] = {}
        self.package_checkboxes: dict[str, tk.Label] = {}
        self._catalog_installed: dict[str, dict] = {}
        self._catalog_signature: tuple[object, ...] | None = None

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
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_high"],
            text_color=COLORS["muted"],
            text_color_disabled=COLORS["muted"],
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
            fg_color=COLORS["button_accent"],
            hover_color=COLORS["button_accent_hover"],
            text_color=COLORS["text"],
            text_color_disabled=COLORS["text"],
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

        self.installed_scroller = FastScrollableFrame(page)
        self.installed_scroller.grid(row=2, column=0, sticky="nsew")
        self.installed_list = self.installed_scroller.content
        self.installed_list.grid_columnconfigure(0, weight=1)
        self.installed_rows: dict[str, tuple[tk.Frame, tk.Label, tk.Label, ctk.CTkButton]] = {}
        self._installed_signature: tuple[object, ...] | None = None

    def _build_downloads(self) -> None:
        page = self._new_page("downloads")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        header = self._page_header(page, self.tr("downloads_title"), self.tr("downloads_subtitle"))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.queue_count = ctk.CTkLabel(
            header,
            text=self.tr("queue_count", count=len(self._queue_snapshot)),
            text_color=COLORS["green"],
            fg_color=COLORS["green_quiet"],
            corner_radius=3,
            padx=10,
            height=28,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.queue_count.grid(row=0, column=1, rowspan=2, padx=(16, 8))
        ctk.CTkButton(
            header,
            text=self.tr("clear_finished"),
            width=104,
            height=36,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=self.install_queue.clear_finished,
        ).grid(row=0, column=2, rowspan=2)

        self.queue_scroller = FastScrollableFrame(page)
        self.queue_scroller.grid(row=1, column=0, sticky="nsew")
        self.queue_list = self.queue_scroller.content
        self.queue_list.grid_columnconfigure(0, weight=1)
        self.queue_rows: dict[str, tuple[tk.Frame, tk.Label, tk.Label, tk.Label, ctk.CTkButton]] = {}
        self._render_download_queue()

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
            placeholder_text=self._game_path_placeholder(),
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
            fg_color=COLORS["button_accent"],
            hover_color=COLORS["button_accent_hover"],
            text_color=COLORS["text"],
            command=self.save_settings,
        ).grid(row=5, column=0, sticky="w", pady=(28, 0))

    def _reload_settings_form(self) -> None:
        if "settings" not in self.pages:
            return
        configured_game_path = self.config.get("game_path", "")
        configured_index_url = self.config.get("index_url", "")
        game_path = configured_game_path.strip() if isinstance(configured_game_path, str) else ""
        index_url = configured_index_url.strip() if isinstance(configured_index_url, str) else ""
        self.game_path_entry.delete(0, "end")
        self.index_source_entry.delete(0, "end")
        if game_path:
            self.game_path_entry.insert(0, game_path)
        if index_url:
            self.index_source_entry.insert(0, index_url)
        self.game_path_entry.configure(
            placeholder_text=self._game_path_placeholder()
        )

    def _game_path_placeholder(self) -> str:
        if not self._detected_game_path_loaded:
            return self.tr("steam_not_found")
        return self._detected_game_path or self.tr("steam_not_found")

    def _start_game_path_detection(self) -> None:
        if self._detected_game_path_loaded:
            return

        def run() -> None:
            detected = detect_game_path()
            try:
                self.after(0, lambda value=detected: self._game_path_detected(value))
            except RuntimeError:
                pass

        threading.Thread(target=run, name="sprocket-steam-detection", daemon=True).start()

    def _game_path_detected(self, value: str | None) -> None:
        self._detected_game_path = value
        self._detected_game_path_loaded = True
        if "settings" in self.pages:
            self.game_path_entry.configure(placeholder_text=self._game_path_placeholder())

    def _build_about(self) -> None:
        page = self._new_page("about")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        header = self._page_header(page, self.tr("about_title"), self.tr("about_subtitle"))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))

        card = ctk.CTkFrame(
            page,
            corner_radius=4,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["line"],
        )
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)

        content = ctk.CTkFrame(card, width=720, fg_color="transparent", corner_radius=0)
        content.grid(row=0, column=0, sticky="new", padx=28, pady=26)
        content.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            content,
            text="SPROCKET MOD MANAGER",
            anchor="w",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))

        details = (
            (self.tr("current_version"), self.version),
            (self.tr("latest_version"), self.tr("checking_updates")),
            (self.tr("source_repository"), MANAGER_REPOSITORY_URL),
            (self.tr("registry_website"), REGISTRY_WEBSITE_URL),
            (self.tr("license"), "AGPL-3.0"),
        )
        detail_values = {}
        for row, (label, value) in enumerate(details, start=1):
            ctk.CTkLabel(
                content,
                text=label.upper(),
                width=150,
                anchor="w",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(family="Cascadia Mono", size=11, weight="bold"),
            ).grid(row=row, column=0, sticky="w", pady=8)
            value_label = ctk.CTkLabel(
                content,
                text=value,
                anchor="w",
                text_color=COLORS["text_soft"],
                font=ctk.CTkFont(size=13),
            )
            value_label.grid(row=row, column=1, sticky="ew", padx=(18, 0), pady=8)
            detail_values[label] = value_label
        self.about_latest_version = detail_values[self.tr("latest_version")]

        actions = ctk.CTkFrame(content, fg_color="transparent", corner_radius=0)
        actions.grid(row=6, column=0, columnspan=2, sticky="w", pady=(24, 0))
        self.about_update_button = ctk.CTkButton(
            actions,
            text=self.tr("checking_updates"),
            width=118,
            height=38,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_high"],
            text_color=COLORS["muted"],
            text_color_disabled=COLORS["muted"],
            command=self.refresh_about_release,
            state="disabled",
        )
        self.about_update_button.grid(row=0, column=0)
        ctk.CTkButton(
            actions,
            text=self.tr("repository"),
            width=118,
            height=38,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=self.open_manager_repository,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            actions,
            text=self.tr("open_registry"),
            width=118,
            height=38,
            corner_radius=3,
            fg_color=COLORS["surface_high"],
            hover_color=COLORS["surface_hover"],
            border_width=1,
            border_color=COLORS["line"],
            command=self.open_registry_website,
        ).grid(row=0, column=2, padx=(8, 0))
        self.refresh_about_release()

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

    def _schedule_package_filter(self) -> None:
        if self._search_after_id is not None:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
        self._search_after_id = self.after(120, self._apply_package_filter)

    def _apply_package_filter(self) -> None:
        self._search_after_id = None
        self.populate_packages()

    def show_page(self, name: str) -> None:
        if name not in self.nav_buttons:
            return
        if name == "settings":
            self.config = self.config_store.load()
        created = name not in self.pages
        self._ensure_page(name)
        if name == "settings" and not created:
            self._reload_settings_form()
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
        if name == "downloads":
            self._render_download_queue()
        if created:
            self._set_busy(self.busy)

    def _set_busy(self, value: bool) -> None:
        self._background_busy = value
        self._refresh_busy_widgets()

    def _set_queue_active(self, value: bool) -> None:
        self._queue_active = value
        self._refresh_busy_widgets()

    def _refresh_busy_widgets(self) -> None:
        self.busy = self._background_busy
        state = "disabled" if self.busy else "normal"
        if "browse" in self.pages:
            self.refresh_button.configure(state=state)
            self._update_batch_button()
            for package_id, checkbox in self.package_checkboxes.items():
                checkbox.configure(
                    state="normal" if not self.busy and self._package_batch_eligible(package_id) else "disabled"
                )
        if "installed" in self.pages:
            _set_primary_button_enabled(self.update_all_button, not self.busy)
        if hasattr(self, "status_indicator"):
            active = self.busy or self._queue_active
            self.status_indicator.configure(text_color=COLORS["accent"] if active else COLORS["green"])
        if hasattr(self, "connection_label"):
            self.connection_label.configure(
                text=self.tr("registry_loading") if self.busy else self.tr("registry_connected"),
                text_color=COLORS["accent"] if self.busy else COLORS["muted"],
            )
        if "browse" not in self.pages:
            return
        if self.busy:
            _set_primary_button_enabled(self.install_button, False)
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
        self._catalog_generation += 1
        generation = self._catalog_generation

        def registry_loaded(service: ModManagerService) -> None:
            try:
                self.after(0, lambda loaded=service: self._catalog_index_loaded(generation, loaded))
            except RuntimeError:
                pass

        def release_loaded(package_id: str, release: ReleaseInfo | None) -> None:
            try:
                self.after(
                    0,
                    lambda item=package_id, value=release: self._catalog_release_loaded(
                        generation, item, value
                    ),
                )
            except RuntimeError:
                pass

        def operation() -> tuple[ModManagerService, dict[str, ReleaseInfo | None]]:
            service = ModManagerService(self.config_store.app_dir)
            return load_catalog(
                service,
                self._source(),
                refresh=force,
                on_registry_loaded=registry_loaded,
                on_release_loaded=release_loaded,
            )

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

    def _catalog_index_loaded(self, generation: int, service: ModManagerService) -> None:
        if generation != self._catalog_generation or not service.registry:
            return
        self.service = service
        package_ids = {package.id for package in service.registry.packages}
        self.latest = {package_id: release for package_id, release in self.latest.items() if package_id in package_ids}
        self.batch_selected.intersection_update(package_ids)
        self.set_status("loading_releases", count=len(package_ids))
        self.populate_packages()

    def _catalog_release_loaded(
        self,
        generation: int,
        package_id: str,
        release: ReleaseInfo | None,
    ) -> None:
        if generation != self._catalog_generation:
            return
        self.latest[package_id] = release
        self._catalog_signature = None
        self._update_package_row(package_id)
        if self.selected and self.selected.id == package_id:
            self.select_package(self.selected)

    def _installed(self) -> dict[str, dict]:
        value = effective_game_path(self.config)
        if not value:
            return {}
        return self.service.installed(Path(value))

    def populate_packages(self) -> None:
        if "browse" not in self.pages:
            return
        registry = self.service.registry
        if not registry:
            self.catalog_count.configure(text=self.tr("package_count", count=0))
            for row in self.package_rows.values():
                row.grid_remove()
            self._update_batch_button()
            return
        keyword = self.search.get().strip().casefold()
        installed = self._installed()
        self._catalog_installed = installed
        package_ids = {package.id for package in registry.packages}
        for package_id in set(self.package_rows) - package_ids:
            self.package_rows.pop(package_id).destroy()
            self.package_row_titles.pop(package_id, None)
            self.package_row_categories.pop(package_id, None)
            self.package_row_metadata.pop(package_id, None)
            self.package_row_surfaces.pop(package_id, None)
            self.package_checkboxes.pop(package_id, None)
            self.batch_selected.discard(package_id)
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
        signature: tuple[object, ...] = (
            self.language,
            self.category,
            keyword,
            self.busy,
            self.selected.id if self.selected else None,
            tuple(sorted(self.batch_selected)),
            tuple(
                (
                    package.id,
                    package.label(self.language),
                    package.category,
                    package.repository,
                    str(self.latest[package.id].version) if self.latest.get(package.id) else None,
                    installed.get(package.id, {}).get("version"),
                    bool(installed.get(package.id, {}).get("requested")),
                )
                for package in visible
            ),
        )
        if signature == self._catalog_signature:
            self._update_batch_button()
            return
        self._catalog_signature = signature
        self._catalog_row_generation += 1
        row_generation = self._catalog_row_generation
        visible_ids = {package.id for package in visible}
        for package_id, row in self.package_rows.items():
            if package_id not in visible_ids:
                row.grid_remove()
        missing_rows: list[tuple[int, str]] = []
        for row_index, package in enumerate(visible):
            row = self.package_rows.get(package.id)
            if row is None:
                missing_rows.append((row_index, package.id))
                continue
            row.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
            self._update_package_row(package.id)
        if missing_rows:
            self.after(
                0,
                lambda rows=tuple(missing_rows): self._create_package_rows_incrementally(
                    row_generation, rows
                ),
            )

        if not visible:
            if not hasattr(self, "no_mods_label"):
                self.no_mods_label = ctk.CTkLabel(
                    self.package_list,
                    text=self.tr("no_mods"),
                    text_color=COLORS["muted"],
                    font=ctk.CTkFont(size=13),
                )
            self.no_mods_label.configure(text=self.tr("no_mods"))
            self.no_mods_label.grid(row=0, column=0, pady=32)
        elif hasattr(self, "no_mods_label"):
            self.no_mods_label.grid_remove()
        self.batch_selected = {
            package_id
            for package_id in self.batch_selected
            if package_id in package_ids and self._package_batch_eligible(package_id)
        }
        self._update_batch_button()

    def _create_package_rows_incrementally(
        self,
        generation: int,
        rows: tuple[tuple[int, str], ...],
    ) -> None:
        if generation != self._catalog_row_generation or not rows or not self.service.registry:
            return
        row_index, package_id = rows[0]
        if package_id not in self.package_rows:
            try:
                package = self.service.registry.get(package_id)
            except ModManagerError:
                package = None
            if package is not None:
                row = self._create_package_row(package)
                row.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
                self._update_package_row(package_id)
        if len(rows) > 1:
            self.after(
                1,
                lambda remaining=rows[1:]: self._create_package_rows_incrementally(
                    generation, remaining
                ),
            )

    def _create_package_row(self, package: RegistryPackage) -> tk.Frame:
        row = tk.Frame(
            self.package_list,
            height=76,
            background=COLORS["surface_high"],
            highlightbackground=COLORS["line_soft"],
            highlightcolor=COLORS["line_soft"],
            highlightthickness=1,
            borderwidth=0,
        )
        row.grid_propagate(False)
        row.grid_columnconfigure(1, weight=1)
        checkbox = tk.Label(
            row,
            text="□",
            width=2,
            height=1,
            borderwidth=0,
            background=COLORS["surface_high"],
            foreground=COLORS["text_soft"],
            disabledforeground=COLORS["muted"],
            activebackground=COLORS["surface_hover"],
            activeforeground=COLORS["text"],
            cursor="hand2",
            takefocus=True,
            font=("Segoe UI", 17, "bold"),
        )
        checkbox.grid(row=0, column=0, rowspan=2, padx=(11, 2), pady=11)
        checkbox.bind(
            "<Button-1>",
            lambda _event, package_id=package.id: self._toggle_batch_selection(package_id),
        )
        checkbox.bind(
            "<space>",
            lambda _event, package_id=package.id: self._toggle_batch_selection(package_id),
        )
        title_row = tk.Frame(row, background=COLORS["surface_high"], borderwidth=0)
        title_row.grid(row=0, column=1, sticky="ew", padx=(4, 11), pady=(9, 1))
        title_row.grid_columnconfigure(0, weight=1)
        title = tk.Label(
            title_row,
            text=package.label(self.language),
            anchor="w",
            foreground=COLORS["text"],
            background=COLORS["surface_high"],
            borderwidth=0,
            font=("Segoe UI", 12, "bold"),
        )
        title.grid(row=0, column=0, sticky="ew")
        category = tk.Label(
            title_row,
            text=self.tr(CATEGORY_KEYS.get(package.category, "category_other")),
            width=10,
            foreground=COLORS["text_soft"],
            background=COLORS["surface_hover"],
            borderwidth=0,
            padx=4,
            pady=1,
            font=("Segoe UI", 11),
        )
        category.grid(row=0, column=1, padx=(7, 0))
        metadata = tk.Label(
            row,
            text="",
            anchor="w",
            foreground="#959D99",
            background=COLORS["surface_high"],
            borderwidth=0,
            font=("Cascadia Mono", 11),
        )
        metadata.grid(row=1, column=1, sticky="ew", padx=(4, 11), pady=(1, 9))
        row.bind("<Button-1>", lambda _event, item=package: self.select_package(item), add="+")
        _bind_click_tree(title_row, lambda item=package: self.select_package(item))
        _bind_click_tree(metadata, lambda item=package: self.select_package(item))
        self.package_rows[package.id] = row
        self.package_row_titles[package.id] = title
        self.package_row_categories[package.id] = category
        self.package_row_metadata[package.id] = metadata
        self.package_row_surfaces[package.id] = (title_row, title, metadata)
        self.package_checkboxes[package.id] = checkbox
        self.package_scroller.bind_mousewheel_tree(row)
        return row

    def _update_package_row(self, package_id: str) -> None:
        if package_id not in self.package_rows or not self.service.registry:
            return
        try:
            package = self.service.registry.get(package_id)
        except ModManagerError:
            return
        release = self.latest.get(package_id)
        install_state = self._catalog_installed.get(package_id)
        state_text = ""
        if install_state:
            key = "installed_state" if install_state.get("requested") else "dependency_state"
            state_text = "  |  " + self.tr(key, version=install_state.get("version", "-"))
        available = str(release.version) if release else "-"
        self.package_row_titles[package_id].configure(text=package.label(self.language))
        self.package_row_categories[package_id].configure(
            text=self.tr(CATEGORY_KEYS.get(package.category, "category_other"))
        )
        self.package_row_metadata[package_id].configure(
            text=f"{package.repository}  |  v{available}{state_text}"
        )
        selected = bool(self.selected and self.selected.id == package_id)
        surface = COLORS["accent_quiet"] if selected else COLORS["surface_high"]
        self.package_rows[package_id].configure(
            background=surface,
            highlightbackground="#513027" if selected else COLORS["line_soft"],
        )
        for widget in self.package_row_surfaces[package_id]:
            widget.configure(background=surface)
        checkbox = self.package_checkboxes[package_id]
        checkbox.configure(
            text="✓" if package_id in self.batch_selected else "□",
            background=COLORS["accent_quiet"] if package_id in self.batch_selected else COLORS["surface_high"],
            foreground=COLORS["text"] if package_id in self.batch_selected else COLORS["text_soft"],
            state="normal" if not self.busy and self._package_batch_eligible(package_id) else "disabled",
        )

    def _package_batch_eligible(self, package_id: str) -> bool:
        release = self.latest.get(package_id)
        if release is None:
            return False
        installed = self._catalog_installed.get(package_id)
        return not installed or installed.get("version") != str(release.version)

    def _toggle_batch_selection(self, package_id: str) -> None:
        if not self._package_batch_eligible(package_id) or self.busy:
            return
        if package_id in self.batch_selected:
            self.batch_selected.remove(package_id)
        else:
            self.batch_selected.add(package_id)
        self._catalog_signature = None
        self._update_package_row(package_id)
        self._update_batch_button()

    def _update_batch_button(self) -> None:
        if "browse" not in self.pages:
            return
        count = len(self.batch_selected)
        self.batch_install_button.configure(text=self.tr("batch_install", count=count))
        _set_primary_button_enabled(self.batch_install_button, count > 0 and not self.busy)

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
        self.remove_button.configure(
            state="normal" if installed and not self.busy and not self._queue_active else "disabled"
        )
        if release and not self.busy:
            action = "update" if installed and installed.get("version") != str(release.version) else "install"
            disabled = bool(installed and installed.get("version") == str(release.version))
            self.install_button.configure(text=self.tr(action))
            _set_primary_button_enabled(self.install_button, not disabled)
        else:
            self.install_button.configure(text=self.tr("install"))
            _set_primary_button_enabled(self.install_button, False)
        for package_id in self.package_rows:
            self._update_package_row(package_id)

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
        if not package:
            return
        self._begin_install_packages([package])

    def begin_batch_install(self) -> None:
        registry = self.service.registry
        if not registry or not self.batch_selected:
            return
        packages = [
            registry.get(package_id)
            for package_id in sorted(self.batch_selected)
        ]
        self._begin_install_packages(packages)

    def _begin_install_packages(self, packages: list[RegistryPackage]) -> None:
        game_path = self._valid_game_path()
        if not packages or not game_path:
            return
        label = packages[0].label(self.language) if len(packages) == 1 else str(len(packages))
        self.set_status("resolving", name=label)

        def operation() -> list[tuple[RegistryPackage, ResolutionPlan]]:
            installed = self.service.installed(game_path)
            plans: list[tuple[RegistryPackage, ResolutionPlan]] = []
            for package in packages:
                plan = self.service.resolve(package.id)
                root = plan.by_id()[package.id]
                if installed.get(package.id, {}).get("version") == str(root.release.version):
                    continue
                plans.append((package, plan))
            return plans

        def resolved(result: object) -> None:
            plans = result
            assert isinstance(plans, list)
            if not plans:
                self.set_status("all_mods_up_to_date")
                self.batch_selected.clear()
                self.populate_packages()
                return
            lines = []
            for package, plan in plans:
                lines.append(f"[{package.label(self.language)}]")
                for item in plan.packages:
                    assets = ", ".join(
                        asset.name for asset in self.service.github.install_assets(item.package, item.release)
                    )
                    lines.append(f"{item.package.label(self.language)} {item.release.tag}\n  {assets}")
            if not ask_confirmation(
                self,
                title=self.tr("title"),
                eyebrow=self.tr("dialog_confirm"),
                message=self.tr(
                    "confirm_install" if len(plans) == 1 else "confirm_batch_install",
                    plan="\n".join(lines),
                ),
                confirm_text=self.tr("confirm"),
                cancel_text=self.tr("cancel"),
                palette=COLORS,
            ):
                return
            added = self.install_queue.enqueue(
                [package.id for package, _plan in plans],
                game_path,
            )
            self.batch_selected.difference_update(entry.package_id for entry in added)
            self.populate_packages()
            self.set_status("installs_queued", count=len(added))
            self.show_page("downloads")

        self._background(operation, resolved)

    def _run_queued_install(
        self,
        entry: InstallQueueEntry,
        progress: Callable[[str], None],
    ) -> None:
        self.service.install(entry.package_id, entry.game_path, progress=progress)

    def _poll_install_queue(self) -> None:
        snapshot = self.install_queue.snapshot()
        if snapshot != self._queue_snapshot:
            previous_states = self._queue_states
            self._queue_snapshot = snapshot
            self._queue_states = {entry.task_id: entry.state for entry in snapshot}
            completed = [
                entry
                for entry in snapshot
                if entry.state == COMPLETED and previous_states.get(entry.task_id) != COMPLETED
            ]
            failed = [
                entry
                for entry in snapshot
                if entry.state == FAILED and previous_states.get(entry.task_id) != FAILED
            ]
            if completed:
                self.populate_packages()
                self.populate_installed()
                latest = completed[-1]
                self.set_status("install_done", name=self._queue_package_label(latest.package_id))
            if failed:
                latest = failed[-1]
                self.set_status(
                    "queue_item_failed",
                    name=self._queue_package_label(latest.package_id),
                    message=latest.message,
                )
            if "downloads" in self.pages:
                self._render_download_queue()
        active = any(entry.state in ACTIVE_STATES for entry in snapshot)
        if active != self._queue_active:
            self._set_queue_active(active)
        try:
            self.after(100, self._poll_install_queue)
        except RuntimeError:
            pass

    def _queue_package_label(self, package_id: str) -> str:
        if self.service.registry:
            try:
                return self.service.registry.get(package_id).label(self.language)
            except ModManagerError:
                pass
        return package_id

    def _render_download_queue(self) -> None:
        if "downloads" not in self.pages:
            return
        self.queue_count.configure(text=self.tr("queue_count", count=len(self._queue_snapshot)))
        task_ids = {entry.task_id for entry in self._queue_snapshot}
        for task_id in set(self.queue_rows) - task_ids:
            frame, *_ = self.queue_rows.pop(task_id)
            frame.destroy()
        if not self._queue_snapshot:
            if not hasattr(self, "queue_empty_label"):
                self.queue_empty_label = ctk.CTkLabel(
                    self.queue_list,
                    text=self.tr("queue_empty"),
                    text_color=COLORS["muted"],
                    font=ctk.CTkFont(size=13),
                )
            self.queue_empty_label.configure(text=self.tr("queue_empty"))
            self.queue_empty_label.grid(row=0, column=0, pady=36)
            return
        if hasattr(self, "queue_empty_label"):
            self.queue_empty_label.grid_remove()
        state_keys = {
            WAITING: "queue_waiting",
            INSTALLING: "queue_installing",
            COMPLETED: "queue_completed",
            FAILED: "queue_failed",
            CANCELED: "queue_canceled",
        }
        state_colors = {
            WAITING: COLORS["muted"],
            INSTALLING: COLORS["accent"],
            COMPLETED: COLORS["green"],
            FAILED: COLORS["danger_hover"],
            CANCELED: COLORS["muted"],
        }
        for row_index, entry in enumerate(self._queue_snapshot):
            widgets = self.queue_rows.get(entry.task_id)
            if widgets is None:
                frame = tk.Frame(
                    self.queue_list,
                    height=76,
                    background=COLORS["surface_high"],
                    highlightbackground=COLORS["line_soft"],
                    highlightthickness=1,
                    borderwidth=0,
                )
                frame.grid_propagate(False)
                frame.grid_columnconfigure(0, weight=1)
                title = tk.Label(
                    frame,
                    text="",
                    anchor="w",
                    foreground=COLORS["text"],
                    background=COLORS["surface_high"],
                    borderwidth=0,
                    font=("Segoe UI", 12, "bold"),
                )
                title.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 1))
                message = tk.Label(
                    frame,
                    text="",
                    anchor="w",
                    foreground=COLORS["muted"],
                    background=COLORS["surface_high"],
                    borderwidth=0,
                    font=("Cascadia Mono", 11),
                )
                message.grid(row=1, column=0, sticky="ew", padx=12, pady=(1, 10))
                state_label = tk.Label(
                    frame,
                    text="",
                    width=10,
                    anchor="e",
                    foreground=COLORS["muted"],
                    background=COLORS["surface_high"],
                    borderwidth=0,
                    font=("Cascadia Mono", 11, "bold"),
                )
                state_label.grid(row=0, column=1, rowspan=2, padx=8)
                cancel_button = ctk.CTkButton(
                    frame,
                    text=self.tr("cancel_queue_item"),
                    width=76,
                    height=32,
                    corner_radius=3,
                    fg_color="transparent",
                    hover_color=COLORS["surface_hover"],
                    border_width=1,
                    border_color=COLORS["line"],
                    command=lambda task_id=entry.task_id: self.install_queue.cancel(task_id),
                )
                widgets = (frame, title, message, state_label, cancel_button)
                self.queue_rows[entry.task_id] = widgets
                self.queue_scroller.bind_mousewheel_tree(frame)
            frame, title, message, state_label, cancel_button = widgets
            frame.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
            title.configure(text=self._queue_package_label(entry.package_id))
            message.configure(text=entry.message or entry.package_id)
            state_label.configure(
                text=self.tr(state_keys.get(entry.state, "queue_waiting")),
                foreground=state_colors.get(entry.state, COLORS["muted"]),
            )
            if entry.state == WAITING:
                cancel_button.configure(text=self.tr("cancel_queue_item"))
                cancel_button.grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=12)
            else:
                cancel_button.grid_remove()

    def begin_remove(self) -> None:
        package = self.selected
        game_path = self._valid_game_path()
        if not package or not game_path or self._queue_active:
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
        installed = self._installed()
        self.installed_count.configure(text=self.tr("installed_count", count=len(installed)))
        signature: tuple[object, ...] = (
            self.language,
            self.busy,
            tuple(
                (
                    package_id,
                    info.get("name"),
                    info.get("version"),
                    bool(info.get("requested")),
                )
                for package_id, info in sorted(installed.items())
            ),
        )
        if signature == self._installed_signature:
            return
        self._installed_signature = signature
        for package_id in set(self.installed_rows) - set(installed):
            frame, *_ = self.installed_rows.pop(package_id)
            frame.destroy()
        if not installed:
            if not hasattr(self, "no_installed_label"):
                self.no_installed_label = ctk.CTkLabel(
                    self.installed_list,
                    text=self.tr("no_installed"),
                    text_color=COLORS["muted"],
                    font=ctk.CTkFont(size=13),
                )
            self.no_installed_label.configure(text=self.tr("no_installed"))
            self.no_installed_label.grid(row=0, column=0, pady=36)
            return
        if hasattr(self, "no_installed_label"):
            self.no_installed_label.grid_remove()
        for row_index, (package_id, info) in enumerate(sorted(installed.items())):
            widgets = self.installed_rows.get(package_id)
            if widgets is None:
                row = tk.Frame(
                    self.installed_list,
                    height=72,
                    background=COLORS["surface_high"],
                    highlightbackground=COLORS["line_soft"],
                    highlightthickness=1,
                    borderwidth=0,
                )
                row.grid_propagate(False)
                row.grid_columnconfigure(0, weight=1)
                title = tk.Label(
                    row,
                    text="",
                    anchor="w",
                    foreground=COLORS["text"],
                    background=COLORS["surface_high"],
                    borderwidth=0,
                    font=("Segoe UI", 12, "bold"),
                )
                title.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 1))
                metadata = tk.Label(
                    row,
                    text="",
                    anchor="w",
                    foreground=COLORS["muted"],
                    background=COLORS["surface_high"],
                    borderwidth=0,
                    font=("Cascadia Mono", 11),
                )
                metadata.grid(row=1, column=0, sticky="ew", padx=12, pady=(1, 10))
                remove_button = ctk.CTkButton(
                    row,
                    text=self.tr("remove"),
                    width=78,
                    height=32,
                    corner_radius=3,
                    fg_color="transparent",
                    hover_color=COLORS["danger"],
                    border_width=1,
                    border_color=COLORS["danger"],
                )
                remove_button.grid(row=0, column=1, rowspan=2, padx=12, pady=12)
                widgets = (row, title, metadata, remove_button)
                self.installed_rows[package_id] = widgets
                self.installed_scroller.bind_mousewheel_tree(row)
            row, title, metadata, remove_button = widgets
            row.grid(row=row_index, column=0, sticky="ew", pady=4, padx=2)
            title.configure(text=info.get("name", package_id))
            key = "requested" if info.get("requested") else "dependency"
            metadata.configure(
                text=f"{package_id}  |  {self.tr('version_label', version=info.get('version', '-'))}  |  {self.tr(key)}",
            )
            package = self.service.registry.get(package_id) if self.service.registry else None
            if package:
                remove_button.configure(
                    text=self.tr("remove"),
                    command=lambda item=package: (self.select_package(item), self.begin_remove()),
                    state="normal" if not self.busy and not self._queue_active else "disabled",
                )
            else:
                remove_button.configure(state="disabled")

    def update_all(self) -> None:
        game_path = self._valid_game_path()
        if not game_path:
            return

        def operation() -> list[str]:
            installed = self.service.installed(game_path)
            updates: list[str] = []
            for package_id, info in installed.items():
                if not info.get("requested"):
                    continue
                plan = self.service.resolve(package_id)
                latest = plan.by_id()[package_id].release.version
                if info.get("version") == str(latest):
                    continue
                updates.append(package_id)
            return updates

        def success(result: object) -> None:
            updates = result
            assert isinstance(updates, list)
            if not updates:
                self.set_status("all_mods_up_to_date")
                return
            added = self.install_queue.enqueue(updates, game_path)
            self.set_status("installs_queued", count=len(added))
            self.show_page("downloads")

        self._background(operation, success)

    def open_repository(self) -> None:
        if self.selected:
            webbrowser.open(f"https://github.com/{self.selected.repository}")

    def open_manager_repository(self) -> None:
        webbrowser.open(MANAGER_REPOSITORY_URL)

    def open_registry_website(self) -> None:
        webbrowser.open(REGISTRY_WEBSITE_URL)

    def refresh_about_release(self) -> None:
        if "about" not in self.pages:
            return
        self.about_latest_version.configure(text=self.tr("checking_updates"))
        self.about_update_button.configure(
            text=self.tr("checking_updates"),
            state="disabled",
            command=self.refresh_about_release,
        )
        github = self.service.github

        def run() -> None:
            try:
                release = github.latest_repository_release(MANAGER_REPOSITORY)
            except DownloadError:
                release = None
            try:
                self.after(0, lambda result=release: self._about_release_loaded(result))
            except RuntimeError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _about_release_loaded(self, release: RepositoryRelease | None) -> None:
        if "about" not in self.pages:
            return
        if release is None:
            self.about_latest_version.configure(text=self.tr("update_unavailable"))
            self.about_update_button.configure(
                text=self.tr("check_updates"),
                state="normal",
                command=self.refresh_about_release,
            )
            return

        self.about_latest_version.configure(text=str(release.version))
        try:
            newer = release.version > Version.parse(self.version)
        except ValueError:
            newer = False
        if newer:
            self.about_update_button.configure(
                text=self.tr("view_update"),
                state="normal",
                fg_color=COLORS["button_accent"],
                hover_color=COLORS["button_accent_hover"],
                text_color=COLORS["text"],
                command=lambda: webbrowser.open(release.page_url),
            )
        else:
            self.about_update_button.configure(
                text=self.tr("manager_up_to_date"),
                state="disabled",
                fg_color=COLORS["surface_high"],
                hover_color=COLORS["surface_high"],
                text_color_disabled=COLORS["muted"],
                command=self.refresh_about_release,
            )

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

    def _request_close(self) -> None:
        if hasattr(self, "install_queue") and self.install_queue.is_installing():
            self.set_raw_status(
                "Wait for the current installation to finish before closing."
                if self.language == "en"
                else "请等待当前安装任务完成后再关闭。"
            )
            self.show_page("downloads")
            return
        self.destroy()

    def destroy(self) -> None:
        if hasattr(self, "install_queue"):
            if self.install_queue.is_installing():
                self._request_close()
                return
            self.install_queue.close()
        super().destroy()


def run_gui(version: str) -> None:
    ModManagerApp(version).mainloop()
