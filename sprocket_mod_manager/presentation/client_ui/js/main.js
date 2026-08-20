"use strict";

function wireEvents() {
    $$(".nav-item").forEach((item) => item.addEventListener("click", () => showPage(item.dataset.pageTarget)));
    $("#catalog-search").addEventListener("input", renderCatalog);
    $("#category-select").addEventListener("change", renderCatalog);
    $("#sort-select").addEventListener("change", renderCatalog);
    $("#translation-search").addEventListener("input", renderCatalog);
    $("#translation-sort").addEventListener("change", renderCatalog);
    $("#refresh-catalog").addEventListener("click", () => loadCatalog(true));
    $("#batch-install").addEventListener("click", () => beginInstall([...state.batch]));
    $("#update-all").addEventListener("click", updateAll);
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
    $("#github-logout-button")?.remove();
    $("#github-login-button").addEventListener("click", handleGithubAuth);
    $("#melonloader-action").addEventListener("click", async () => {
        const editedPath = $("#game-path").value.trim();
        if (editedPath !== (state.settings.game_path || "") && !(await saveSettings())) return;
        if (!state.melonloader || state.melonloader.error) await refreshMelonLoaderStatus(true);
        else await installMelonLoader();
    });
    $("#open-melonloader-release").addEventListener("click", () => {
        openUrl(state.melonloader?.page_url || state.links.melonloader);
    });
    $("#upload-latest-log").addEventListener("click", uploadLatestLog);
    $("#text-scale").addEventListener("input", (event) => {
        applyTextScale(event.target.value);
        scheduleSettingsSave();
    });
    $("#manager-update").addEventListener("click", () => {
        if (state.update?.newer) void openUrl(state.update.page_url);
        else void checkManagerUpdate(false);
    });
    $("#open-repository").addEventListener("click", () => openUrl(state.links.repository));
    $("#open-registry").addEventListener("click", () => openUrl(state.links.registry));
    $("#open-manager-directory").addEventListener("click", openManagerDirectory);
    $("#upload-manager-log").addEventListener("click", uploadManagerLog);
    $("#modal-close").addEventListener("click", () => closeModal(false));
    $("#modal-cancel").addEventListener("click", () => closeModal(false));
    $("#modal-confirm").addEventListener("click", () => closeModal(true));
    $("#modal-layer").addEventListener("click", (event) => {
        if (event.target === $("#modal-layer") && state.modalCloseOnBackdrop !== false) closeModal(false);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !$("#modal-layer").hidden) closeModal(false);
    });
}

async function initialize() {
    if (state.ready || state.initializing) return;
    state.initializing = true;
    wireEvents();
    try {
        await callApi("startup_trace", "initialize entered");
        const result = await callApi("bootstrap");
        await callApi("startup_trace", "bootstrap resolved");
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
        await callApi("startup_trace", "initial UI state rendered");
        await pollQueue(true);
        await callApi("startup_trace", "initial queue loaded");
        void detectGamePathPlaceholder();
        void loadCatalog(false);
        window.setTimeout(() => {
            void checkManagerUpdate(true);
        }, 350);
        window.setInterval(() => {
            void pollQueue(false);
        }, 400);
    } catch (error) {
        reportClientLog("error", `client initialization failed: ${String(error)}`);
        state.ready = false;
        setRegistryState("error", tr("connectionFailed"));
        resultError({message: String(error)});
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
