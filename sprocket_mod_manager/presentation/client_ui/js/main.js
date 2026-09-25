"use strict";

function wireEvents() {
    // 只接一次：初始化如果中途失败会被再调一次，接两遍就等于点一下触发两次。
    if (state.wired) return;
    state.wired = true;
    $$(".nav-item").forEach((item) => item.addEventListener("click", () => showPage(item.dataset.pageTarget)));
    $("#catalog-search").addEventListener("input", renderCatalog);
    $("#category-select").addEventListener("change", renderCatalog);
    $("#sort-select").addEventListener("change", renderCatalog);
    $("#translation-search").addEventListener("input", renderCatalog);
    $("#translation-sort").addEventListener("change", renderCatalog);
    $("#refresh-catalog").addEventListener("click", () => loadCatalog(true));
    $$("[data-selection-action]").forEach((button) => {
        button.addEventListener("click", () => void handleCatalogSelection(button.dataset.selectionAction));
    });
    $$("[data-installed-filter]").forEach((chip) => {
        chip.addEventListener("click", () => setInstalledFilter(chip.dataset.installedFilter));
    });
    $("#update-selected").addEventListener("click", updateSelectedMods);
    $("#disable-selected").addEventListener("click", () => toggleSelectedMods(false));
    $("#enable-selected").addEventListener("click", () => toggleSelectedMods(true));
    $("#remove-selected").addEventListener("click", removeSelectedMods);
    $("#toggle-selection").addEventListener("click", toggleInstalledSelection);
    $("#invert-selection").addEventListener("click", invertInstalledSelection);
    $("#clear-finished").addEventListener("click", async () => {
        await callApi("clear_finished");
        await pollQueue(true);
    });
    $("#browse-game-path").addEventListener("click", chooseGamePath);
    ["#debug-mode", "#proxy-enabled", "#github-proxy-enabled", "#text-scale"]
        .forEach((selector) => $(selector).addEventListener("change", () => scheduleSettingsSave(0)));
    $("#language-select").addEventListener("change", (event) => {
        state.languageMode = event.target.value;
        scheduleSettingsSave(0);
    });
    ["#game-path", "#index-url", "#proxy-url", "#github-proxy-url"]
        .forEach((selector) => $(selector).addEventListener("input", () => scheduleSettingsSave()));
    $("#proxy-enabled").addEventListener("change", syncProxyControls);
    $("#github-proxy-enabled").addEventListener("change", syncProxyControls);
    $("#add-developer-server").addEventListener("click", addDeveloperServer);
    $("#github-login-button").addEventListener("click", handleGithubAuth);
    $("#environment-install-loader").addEventListener("click", () => void showPage("modloaders"));
    $("#kill-sprocket").addEventListener("click", () => void killRunningSprocket());
    $("#text-scale").addEventListener("input", (event) => {
        applyTextScale(event.target.value);
        scheduleSettingsSave();
    });
    $("#manager-update").addEventListener("click", () => {
        if (!state.update?.newer) {
            void checkManagerUpdate(false);
            return;
        }
        if (state.update.can_self_update) void applyManagerUpdate(state.update);
        else void openUrl(state.update.page_url);
    });
    $("#open-repository").addEventListener("click", () => openUrl(state.links.repository));
    $("#open-registry").addEventListener("click", () => openUrl(state.links.registry));
    $("#open-manager-directory").addEventListener("click", openManagerDirectory);
    $("#upload-logs").addEventListener("click", () => void openLogPicker());
    $("#modal-close").addEventListener("click", () => closeModal(false));
    $("#modal-cancel").addEventListener("click", () => closeModal(false));
    $("#modal-confirm").addEventListener("click", () => closeModal(true));
    $("#modal-layer").addEventListener("click", (event) => {
        if (event.target === $("#modal-layer") && state.modalCloseOnBackdrop !== false) closeModal(false);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        if (!$("#modal-layer").hidden) closeModal(false);
    });
}

async function initialize() {
    if (state.ready || state.initializing) return;
    state.initializing = true;
    wireEvents();
    try {
        traceStartup("initialize entered");
        const result = await callApi("bootstrap");
        traceStartup("bootstrap resolved");
        if (!result.ok) throw new Error(result.message || "bootstrap failed");
        state.ready = true;
        reportClientLog("info", `client initialized version=${result.version}`);
        state.version = result.version;
        state.settings = result.settings;
        state.languageMode = result.settings.language;
        state.links = result.links;
        $("#debug-mode").checked = result.settings.debug;
        $("#current-version").textContent = state.version;
        $("#game-path").value = result.settings.game_path;
        $("#index-url").value = result.settings.index_url;
        $("#index-url").placeholder = result.settings.index_placeholder;
        $("#proxy-enabled").checked = result.settings.proxy_enabled;
        $("#proxy-url").value = result.settings.proxy_url;
        $("#proxy-url").placeholder = result.settings.proxy_placeholder;
        $("#github-proxy-enabled").checked = result.settings.github_proxy_enabled;
        $("#github-proxy-url").value = result.settings.github_proxy_url;
        $("#github-proxy-url").placeholder = result.settings.github_proxy_placeholder;
        syncProxyControls();
        applyTextScale(result.settings.text_scale);
        renderGithubLogin();
        setLanguage(result.language);
        void refreshEnvironment(true);
        traceStartup("initial UI state rendered");
        // 管理器是「装了什么」的工具：启动直接进安装管理页，目录页留给找新模组的时候。
        await showPage("installed");
        await pollQueue(true);
        traceStartup("initial queue loaded");
        void detectGamePathPlaceholder();
        // 注册表随目录一起加载，加载器清单由它给出：目录回来后再读一次环境，左下角才不会
        // 停在启动时那份「还没有注册表」的读数上。
        void loadCatalog(false).then(() => refreshEnvironment(false));
        window.setTimeout(() => {
            void checkManagerUpdate(true);
        }, 350);
        window.setInterval(() => {
            void pollQueue(false);
        }, 400);
        window.setInterval(() => {
            void pollEnvironment();
        }, 1000);
    } catch (error) {
        reportClientLog("error", `client initialization failed: ${String(error)}`);
        state.ready = false;
        setRegistryState("error", tr("connectionFailed"));
        resultError({code: "client_startup_failed", message: String(error)});
    } finally {
        state.initializing = false;
    }
}

window.addEventListener("pywebviewready", initialize, {once: true});
window.addEventListener("error", (event) => {
    reportClientLog("error", `window error: ${event.message || "unknown error"}`);
    if (!$("#modal-layer").hidden) closeModal(false);
    toast(event.message || tr("operationFailed"), "error");
});
window.addEventListener("unhandledrejection", (event) => {
    reportClientLog("error", `unhandled rejection: ${String(event.reason || "unknown rejection")}`);
    if (!$("#modal-layer").hidden) closeModal(false);
    toast(String(event.reason || tr("operationFailed")), "error");
});

if (window.pywebview?.api) {
    void initialize();
} else {
    // Some WebView2 builds inject the API before dispatching (or race) the
    // pywebviewready event. Keep a short fallback probe so the static page
    // cannot remain on "Starting" forever.
    const readyProbe = window.setInterval(() => {
        if (!window.pywebview?.api) return;
        window.clearInterval(readyProbe);
        void initialize();
    }, 100);
    window.setTimeout(() => window.clearInterval(readyProbe), 30000);
}
