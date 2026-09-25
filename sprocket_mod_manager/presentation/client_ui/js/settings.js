"use strict";

let settingsSaveTimer = null;

function scheduleSettingsSave(delay = 350) {
    window.clearTimeout(settingsSaveTimer);
    settingsSaveTimer = window.setTimeout(() => {
        settingsSaveTimer = null;
        void saveSettings();
    }, delay);
}

async function saveSettings(values = null) {
    const previousGamePath = state.settings.game_path || "";
    const payload = values || {
        debug: $("#debug-mode").checked,
        language: state.languageMode,
        game_path: $("#game-path").value.trim(),
        index_url: $("#index-url").value.trim(),
        proxy_enabled: $("#proxy-enabled").checked,
        proxy_url: $("#proxy-url").value.trim(),
        github_proxy_enabled: $("#github-proxy-enabled").checked,
        github_proxy_url: $("#github-proxy-url").value.trim(),
        text_scale: applyTextScale($("#text-scale").value),
    };
    const result = await callApi("save_settings", payload);
    if (!result.ok) {
        resultError(result);
        return false;
    }
    state.settings = result.settings;
    if (previousGamePath !== (result.settings.game_path || "")) {
        // 换了游戏目录：上一个目录的读数由**数据层**作废并重取（见 `save_settings`），
        // 界面只复位自己那份视图状态，再让环境 / 目录 / 已安装各刷一次。
        state.environmentAxesKey = null;
        await refreshEnvironment(true);
        await loadCatalog(false);
        await refreshInstalled();
        await refreshModloaders();
    }
    state.languageMode = result.settings.language;
    applyTextScale(result.settings.text_scale);
    setLanguage(result.language);
    toast(tr("settingsSaved"));
    setStatus("", "ready");
    return true;
}

async function chooseGamePath() {
    const result = await callApi("choose_game_path");
    if (!result.ok) {
        resultError(result);
        return;
    }
    if (result.path) $("#game-path").value = result.path;
    if (result.path) scheduleSettingsSave(0);
}

async function openManagerDirectory() {
    const result = await callApi("open_manager_directory");
    if (!result.ok) {
        resultError(result);
        return;
    }
    toast(tr("managerDirectoryOpened"));
    setStatus(tr("managerDirectoryOpened"), "ready", result.path || "");
}

async function detectGamePathPlaceholder() {
    const result = await callApi("find_game_path");
    if (!result.ok) return;
    $("#game-path").placeholder = result.path || tr("steamNotFound");
    updatePageHeader();
}
