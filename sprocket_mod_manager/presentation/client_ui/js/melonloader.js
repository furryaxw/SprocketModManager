"use strict";

function renderMelonLoader() {
    const status = $("#melonloader-state");
    const detail = $("#melonloader-detail");
    const action = $("#melonloader-action");
    const release = $("#open-melonloader-release");
    if (!status || !detail || !action || !release) return;

    status.className = "component-state";
    release.disabled = !state.links.melonloader && !state.melonloader?.page_url;
    if (state.melonloaderLoading) {
        status.textContent = tr("checking");
        detail.textContent = tr("melonloaderChecking");
        action.textContent = tr("checking");
        action.disabled = true;
        return;
    }
    if (!state.melonloader) {
        status.textContent = tr("checking");
        detail.textContent = tr("melonloaderChecking");
        action.textContent = tr("checkStatus");
        action.disabled = false;
        return;
    }
    if (state.melonloader.error) {
        status.classList.add("error");
        status.textContent = tr("errorLabel");
        detail.textContent = state.melonloader.errorCode === "game_path_required"
            ? tr("melonloaderPathRequired")
            : state.melonloader.error;
        action.textContent = tr("checkStatus");
        action.disabled = false;
        return;
    }

    const installed = state.melonloader.installed_version || tr("versionUnknown");
    const latest = state.melonloader.latest_version || tr("versionUnknown");
    if (!state.melonloader.installed) {
        status.classList.add("missing");
        status.textContent = tr("notInstalled");
        detail.textContent = tr("melonloaderMissing");
        action.textContent = tr("installMelonLoader");
    } else if (state.melonloader.update_available) {
        status.classList.add("update");
        status.textContent = tr("updateLabel");
        detail.textContent = tr("melonloaderUpdateReady", {installed, latest});
        action.textContent = tr("updateMelonLoader");
    } else {
        status.classList.add("ready");
        status.textContent = tr("installedLabel");
        detail.textContent = state.melonloader.installed_version
            ? tr("melonloaderInstalled", {installed, latest})
            : tr("melonloaderInstalledUnknown", {latest});
        action.textContent = tr("reinstallMelonLoader");
    }
    action.disabled = queueActive();
}

async function refreshMelonLoaderStatus(refresh = false) {
    state.melonloaderLoading = true;
    renderMelonLoader();
    try {
        const result = await callApi("get_melonloader_status", true, refresh);
        if (!result.ok) {
            state.melonloader = {
                error: result.message || tr("melonloaderUnavailable"),
                errorCode: result.code || "melonloader_status_failed",
            };
            return false;
        }
        state.melonloader = result.melonloader;
        return true;
    } catch (error) {
        state.melonloader = {error: String(error), errorCode: "melonloader_status_failed"};
        return false;
    } finally {
        state.melonloaderLoading = false;
        renderMelonLoader();
    }
}

async function installMelonLoader() {
    state.melonloaderLoading = true;
    renderMelonLoader();
    setStatus(tr("melonloaderInstalling"));
    try {
        const result = await callApi("install_melonloader", true);
        if (!result.ok) {
            resultError(result);
            return false;
        }
        state.melonloader = result.melonloader;
        const message = tr("melonloaderInstalledDone", {
            version: result.melonloader.latest_version || tr("versionUnknown"),
            count: result.files_installed,
        });
        toast(message);
        setStatus(message, "ready");
        return true;
    } catch (error) {
        resultError({message: String(error)});
        return false;
    } finally {
        state.melonloaderLoading = false;
        renderMelonLoader();
    }
}

async function openUrl(url) {
    const result = await callApi("open_url", url);
    if (!result.ok) resultError(result);
}

async function checkManagerUpdate(startup) {
    const button = $("#manager-update");
    button.disabled = true;
    button.textContent = tr("checkingUpdate");
    $("#latest-version").textContent = "...";
    try {
        const result = await callApi("get_manager_update");
        if (!result.ok) {
            $("#latest-version").textContent = tr("updateUnavailable");
            button.textContent = tr("checkUpdate");
            button.disabled = false;
            if (!startup) resultError(result);
            return;
        }
        state.update = result;
        $("#latest-version").textContent = result.latest;
        if (result.newer) {
            button.disabled = false;
            button.textContent = tr("getUpdate");
            if (startup && $("#modal-layer").hidden) {
                const confirmed = await showModal({
                    kicker: tr("updateAvailableKicker"),
                    title: tr("updateFound"),
                    body: tr("updateMessage", {latest: result.latest, current: result.current}),
                    confirmText: tr("getUpdate"),
                    cancelText: tr("later"),
                });
                if (confirmed) await openUrl(result.page_url);
            }
        } else {
            button.textContent = tr("upToDate");
            button.disabled = true;
        }
    } catch (error) {
        $("#latest-version").textContent = tr("updateUnavailable");
        button.textContent = tr("checkUpdate");
        button.disabled = false;
        if (!startup) resultError({message: String(error)});
    }
}
