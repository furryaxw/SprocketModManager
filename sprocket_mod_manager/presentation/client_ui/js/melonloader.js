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

async function refreshEnvironment(includeLatest = false) {
    try {
        const result = await callApi("get_environment", includeLatest);
        state.environment = result.ok ? result : null;
    } catch (_error) {
        // 环境信息读不到就不显示版本，别让左下角冒红：本机情况在列表里有更准确的呈现。
        state.environment = null;
    }
    if (state.environment) state.environmentRevision = state.environment.revision;
    renderEnvironment();
}

/** 环境里真正影响判定的那几项：只有它们变了才值得重新拉目录。 */
function environmentKey(environment) {
    if (!environment) return "";
    return [
        environment.sprocket?.version || environment.sprocket?.state || "",
        environment.melonloader?.used_version || "",
        environment.environment?.state || "",
    ].join("|");
}

/**
 * 每秒问一次环境。
 *
 * `revision` 变了说明游戏目录里动过东西（游戏更新、加载器装/卸、Mods 里增删文件）：
 * 版本或判定口径变了就重拉目录（每个 release 的判定是后端按环境算的），只是文件变了就刷新列表。
 */
async function pollEnvironment() {
    if (!state.ready) return;
    let result;
    try {
        result = await callApi("get_environment", false);
    } catch (_error) {
        return;
    }
    if (!result.ok) return;
    const previousKey = environmentKey(state.environment);
    const changed = state.environmentRevision !== result.revision;
    state.environmentRevision = result.revision;
    state.environment = result;
    renderStatusbar();
    if (!changed) return;
    renderEnvironment();
    if (environmentKey(result) !== previousKey) await loadCatalog(false);
    else await refreshInstalled();
}

/**
 * 左下角：两行版本 + 「为什么对不上」那一行。
 *
 * 状态栏（statusbar）那边只报健康状态，细节都在这里 —— 这区不弹 toast。
 */
function renderEnvironment() {
    const sprocket = $("#environment-sprocket");
    const loader = $("#environment-melonloader-text");
    const install = $("#environment-install-melonloader");
    const note = $("#environment-note");
    if (!sprocket || !loader || !install || !note) return;

    const environment = state.environment;
    const sprocketInfo = environment?.sprocket || {};
    const loaderInfo = environment?.melonloader;
    const problem = environmentProblem();

    sprocket.classList.toggle("error",
        sprocketInfo.state === "legacy" || sprocketInfo.state === "unreadable" || Boolean(problem));
    const sprocketText = sprocketInfo.state === "legacy"
        ? (sprocketInfo.raw || "-")
        : sprocketInfo.state === "ok" ? (sprocketInfo.version || "-") : "-";
    sprocket.textContent = `Sprocket ${sprocketText}`;

    loader.hidden = Boolean(environment) && !loaderInfo?.installed;
    loader.textContent = !environment
        ? "MelonLoader ..."
        : loaderInfo?.installed ? `MelonLoader ${loaderInfo.version || tr("versionUnknown")}` : "";
    install.hidden = !environment || Boolean(loaderInfo?.installed)
        || sprocketInfo.state === "unconfigured";
    install.disabled = queueActive() || state.melonloaderLoading;

    note.hidden = !problem;
    note.className = "environment-line environment-note error";
    note.textContent = problem;
    // 环境是状态栏要看的活状态之一，顺手重算一次。
    renderStatusbar();
}

/**
 * 环境哪里不对：返回一句给 toast 的说明，没问题就返回空串。
 *
 * 三类：环境自身矛盾（加载器跟不上游戏）、游戏版本太老/读不出来、游戏路径还没配好。
 */
function environmentProblem() {
    const environment = state.environment;
    if (!environment) return "";
    const sprocket = environment.sprocket || {};
    if (sprocket.state === "legacy" || sprocket.state === "unreadable") {
        return tr("environmentUnusable", {raw: sprocket.raw || "-"});
    }
    if (environment.environment?.state === "conflict") {
        return tr("environmentConflict", {
            loader: environment.melonloader?.used_version || tr("versionUnknown"),
            sprocket: sprocket.version || "-",
        });
    }
    return "";
}

/** 环境变坏时才弹一次（每次轮询都弹会刷屏）—— 见 `renderEnvironment` 里的比较。 */

/**
 * 左下角和设置页共用同一个动作：先把可能改过的游戏路径落盘，再决定是查状态还是直接装。
 */
async function handleMelonLoaderAction() {
    const editedPath = $("#game-path").value.trim();
    if (editedPath !== (state.settings.game_path || "") && !(await saveSettings())) return;
    if (!state.melonloader || state.melonloader.error) await refreshMelonLoaderStatus(true);
    else await installMelonLoader();
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

/**
 * 装 MelonLoader 之前先查环境表：这段加载器版本还不支持本机游戏版本时，先说清楚再问一次。
 *
 * 表只从注册表来（`state.environment.environment.state` 是后端按表算好的），没装加载器时
 * 用它算的是**最新版**能不能跑 —— 正好是「装上去有没有用」的答案。
 */
async function confirmIncompatibleLoader() {
    if (!state.environment) return true;
    if (state.environment.melonloader?.installed) return true;
    if (!state.environment.melonloader?.latest_version) await refreshEnvironment(true);
    const environment = state.environment;
    if (environment?.environment?.state !== "conflict") return true;
    return showModal({
        kicker: tr("modRuntime"),
        title: tr("melonloaderIncompatibleTitle"),
        body: tr("melonloaderIncompatibleMessage", {
            version: environment.melonloader?.used_version || tr("versionUnknown"),
            sprocket: environment.sprocket?.version || environment.sprocket?.raw || "-",
        }),
        confirmText: tr("installAnyway"),
        cancelText: tr("cancel"),
    });
}

async function installMelonLoader(skipCompatibilityCheck = false) {
    if (!skipCompatibilityCheck && !(await confirmIncompatibleLoader())) return false;
    state.melonloaderLoading = true;
    renderMelonLoader();
    setStatus(tr("melonloaderInstalling"));
    let installed = false;
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
        setStatus("", "ready");
        if (result.compatibility?.state === "conflict") {
            // 装是装上了，但按表它跑不了这个游戏版本：说清楚，别让人以为装完就能用。
            toast(tr("melonloaderStillIncompatible", {
                version: result.melonloader.installed_version || tr("versionUnknown"),
                sprocket: result.compatibility.sprocket || "-",
            }), "error");
        }
        installed = true;
        return true;
    } catch (error) {
        resultError({message: String(error)});
        return false;
    } finally {
        state.melonloaderLoading = false;
        renderMelonLoader();
        // 装完左下角那行也要跟着变（放在 finally 里，避免按钮停留在"检查中"的禁用态）
        if (installed) void refreshEnvironment(true);
    }
}

async function openUrl(url) {
    const result = await callApi("open_url", url);
    if (!result.ok) resultError(result);
}

/**
 * 管理器自己的版本检查。
 *
 * 打包成单文件时可以直接换掉自己：确认后下载新版、交给换壳子进程重启（后端关掉这个窗口）。
 * 选「稍后」＝这次会话先不管，下次启动照样问（不落任何持久化）。
 */
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
        if (!result.newer) {
            button.textContent = tr("upToDate");
            button.disabled = true;
            return;
        }
        button.disabled = false;
        button.textContent = result.can_self_update ? tr("updateNow") : tr("getUpdate");
        if (startup && $("#modal-layer").hidden) {
            const confirmed = await showModal({
                kicker: tr("updateAvailableKicker"),
                title: tr("updateFound"),
                body: managerUpdateBody(result),
                confirmText: result.can_self_update ? tr("updateNow") : tr("getUpdate"),
                cancelText: tr("later"),
            });
            if (!confirmed) return;
            if (result.can_self_update) await applyManagerUpdate(result);
            else await openUrl(result.page_url);
        }
    } catch (error) {
        $("#latest-version").textContent = tr("updateUnavailable");
        button.textContent = tr("checkUpdate");
        button.disabled = false;
        if (!startup) resultError({message: String(error)});
    }
}

/** 更新弹窗那块内容：版本对比 + 发布说明（源码运行时再说明一句为什么不能自助更新）。 */
function managerUpdateBody(update) {
    const body = document.createElement("div");
    body.className = "update-body";
    const versions = document.createElement("p");
    versions.textContent = tr("updateMessage", {latest: update.latest, current: update.current});
    body.append(versions);
    const notes = String(update.notes || "").trim();
    if (notes) {
        const block = document.createElement("pre");
        block.className = "update-notes";
        block.textContent = notes;
        body.append(block);
    }
    if (!update.can_self_update) {
        const hint = document.createElement("p");
        hint.className = "modal-note";
        hint.textContent = tr("updateSelfUpdateUnavailable");
        body.append(hint);
    }
    return body;
}

/** 立即更新：下载新版 → 换壳重启。成功的表现是这个窗口被后端关掉。 */
async function applyManagerUpdate(update) {
    void showModal({
        kicker: tr("updateAvailableKicker"),
        title: tr("updateDownloading"),
        body: tr("updateDownloadingMessage", {version: update.latest}),
        confirmText: tr("close"),
        cancelText: null,
        closeOnBackdrop: false,
    });
    const modalStatus = $("#modal-status");
    if (modalStatus) {
        modalStatus.hidden = false;
        modalStatus.textContent = tr("updateDownloading");
    }
    const applied = await callApi("apply_manager_update");
    if (!applied.ok) {
        closeModal(false);
        resultError(applied);
        return;
    }
    const message = tr("updateRestarting", {version: applied.version});
    if (modalStatus) modalStatus.textContent = message;
    setStatus(message);
}
