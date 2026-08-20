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
    if (previousGamePath !== (result.settings.game_path || "")) state.melonloader = null;
    state.languageMode = result.settings.language;
    applyTextScale(result.settings.text_scale);
    setLanguage(result.language);
    toast(tr("settingsSaved"));
    setStatus(tr("settingsSaved"), "ready");
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

async function uploadLog(buttonSelector, apiMethod, confirmMessageKey) {
    const button = $(buttonSelector);
    const confirmed = await showModal({
        kicker: tr("logUpload"),
        title: tr("uploadConfirmTitle"),
        body: tr(confirmMessageKey),
        confirmText: tr("confirm"),
        closeOnBackdrop: false,
    });
    if (!confirmed) return;
    if (button) button.disabled = true;
    try {
        const result = await callApi(apiMethod);
        if (!result.ok) {
            resultError(result);
            return;
        }
        const body = document.createElement("div");
        body.className = "log-upload-link-row";
        const link = document.createElement("input");
        link.type = "text";
        link.readOnly = true;
        link.value = result.url;
        link.className = "field-input";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "secondary-button";
        copy.textContent = tr("copyLink");
        copy.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(result.url);
            } catch {
                link.select();
                document.execCommand("copy");
            }
            copy.textContent = tr("copied");
            toast(tr("copied"));
        });
        body.append(link, copy);
        showModal({
            kicker: tr("logUpload"),
            title: tr("uploadDoneTitle"),
            body,
            confirmText: tr("close"),
            closeOnBackdrop: false,
        });
        $("#modal-cancel").hidden = true;
        link.focus();
        link.select();
    } finally {
        if (button) button.disabled = false;
    }
}

async function uploadLatestLog() {
    await uploadLog("#upload-latest-log", "upload_latest_log", "uploadConfirmMessage");
}

async function uploadManagerLog() {
    await uploadLog("#upload-manager-log", "upload_manager_log", "uploadManagerLogConfirmMessage");
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
