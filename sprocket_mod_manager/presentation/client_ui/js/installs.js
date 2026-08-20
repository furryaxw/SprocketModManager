"use strict";

async function beginInstall(packageIds) {
    if (!packageIds.length) return;
    setStatus(tr("resolving"));
    const result = await callApi("plan_install", packageIds);
    if (!result.ok) {
        if (result.code === "game_path_required") {
            showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
        } else resultError(result);
        return;
    }
    if (!result.plans.length) {
        toast(tr("nothingToInstall"));
        setStatus(tr("nothingToInstall"), "ready");
        state.batch.clear();
        renderCatalog();
        return;
    }
    const planBody = createPlanBody(result.plans, result.recommendations || []);
    const confirmed = await showModal({
        kicker: tr("installPlan"),
        title: result.plans.length === 1 ? tr("confirmInstall") : tr("confirmBatchInstall"),
        body: planBody,
        confirmText: tr("confirm"),
    });
    if (!confirmed) return;
    const loaderDecision = await ensureMelonLoader(Boolean(result.melonloader_installed));
    if (!loaderDecision.proceed) return;
    const queued = await callApi(
        "enqueue_install",
        [
            ...result.plans.map((plan) => plan.id),
            ...$$('input[type="checkbox"][data-package-id]:checked', planBody)
                .map((input) => input.dataset.packageId),
        ],
        loaderDecision.allowWithout,
    );
    if (!queued.ok) {
        resultError(queued);
        return;
    }
    state.batch.clear();
    renderCatalog();
    toast(tr("queued", {count: queued.count}));
    setStatus(tr("queued", {count: queued.count}), "ready");
    await pollQueue(true);
    await showPage("downloads");
}

async function refreshInstalled() {
    if (!state.ready) return;
    try {
        const result = await callApi("get_installed");
        if (!result.ok) {
            resultError(result);
            return;
        }
        state.installed = result.installed || [];
        state.unrecognized = result.unrecognized || [];
        state.hasAnyMods = Boolean(result.has_any_mods);
        const installedById = new Map(state.installed.map((item) => [item.id, item]));
        for (const pkg of state.packages) pkg.installed = installedById.get(pkg.id) || null;
        renderInstalled();
        renderCatalog();
        notifyAdopted(result.adopted);
    } catch (error) {
        resultError({message: String(error)});
    }
}

function renderInstalled() {
    const container = $("#installed-list");
    if (!container) return;
    const items = [
        ...state.installed.map((item) => ({...item, unrecognized: false})),
        ...state.unrecognized.map((item) => ({...item, unrecognized: true})),
    ];
    $("#installed-count").textContent = tr("detectedMods", {count: items.length});
    container.replaceChildren();
    if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty-list";
        const title = document.createElement("strong");
        title.textContent = tr("noInstalled");
        empty.append(title);
        container.append(empty);
        return;
    }
    for (const item of items) {
        const pkg = item.unrecognized
            ? null
            : state.packages.find((candidate) => candidate.id === item.id);
        const row = document.createElement("article");
        row.className = "data-row";
        const title = document.createElement("div");
        title.className = "row-title";
        const name = document.createElement("strong");
        name.textContent = item.unrecognized
            ? item.name
            : pkg ? packageLabel(pkg) : item.name || item.id;
        const metadata = document.createElement("span");
        if (item.unrecognized) {
            metadata.textContent = item.path;
        } else {
            const source = item.adopted ? tr("adopted") : item.requested ? tr("requested") : tr("dependency");
            metadata.textContent = `${item.id}  |  ${item.version || "-"}  |  ${source}`;
        }
        title.append(name, metadata);
        const actions = document.createElement("div");
        actions.className = "row-actions";
        if (item.unrecognized) {
            const status = document.createElement("span");
            status.className = "state-chip unrecognized";
            status.textContent = tr("unrecognized");
            actions.append(status);
        } else {
            const removablePackage = pkg || (item.id.includes(":") ? {
                id: item.id,
                name: item.name || item.id,
                display_name: {en: item.name || item.id, zh: item.name || item.id},
            } : null);
            const remove = document.createElement("button");
            remove.className = "danger-button";
            remove.type = "button";
            remove.textContent = tr("remove");
            remove.disabled = !removablePackage || queueActive();
            remove.addEventListener("click", () => removablePackage && confirmRemove(removablePackage));
            actions.append(remove);
        }
        row.append(title, actions);
        container.append(row);
    }
}

async function confirmRemove(pkg) {
    const confirmed = await showModal({
        kicker: tr("removePackage"),
        title: tr("confirmRemove"),
        body: tr("removeMessage", {name: packageLabel(pkg)}),
        confirmText: tr("remove"),
        destructive: true,
    });
    if (!confirmed) return;
    const result = await callApi("remove", pkg.id);
    if (!result.ok) {
        resultError(result);
        return;
    }
    const message = tr("removed", {names: result.removed.join(", ")});
    toast(result.warnings?.length ? `${message} | ${result.warnings.join("; ")}` : message);
    setStatus(message, "ready");
    await refreshInstalled();
}

async function updateAll() {
    const button = $("#update-all");
    button.disabled = true;
    try {
        const hasUpdates = state.installed.some((item) => {
            if (!item.requested) return false;
            const pkg = state.packages.find((candidate) => candidate.id === item.id);
            return Boolean(pkg?.release && item.version !== pkg.release.version);
        });
        if (!hasUpdates) {
            toast(tr("noUpdates"));
            setStatus(tr("noUpdates"), "ready");
            return;
        }
        const loaderDecision = await ensureMelonLoader(null);
        if (!loaderDecision.proceed) return;
        const result = await callApi("update_all", loaderDecision.allowWithout);
        if (!result.ok) {
            if (result.code === "game_path_required") {
                showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
            } else resultError(result);
            return;
        }
        if (!result.count) {
            toast(tr("noUpdates"));
            setStatus(tr("noUpdates"), "ready");
            return;
        }
        toast(tr("updateQueued", {count: result.count}));
        await pollQueue(true);
        await showPage("downloads");
    } finally {
        button.disabled = false;
    }
}

function queueActive() {
    return state.queue.some((entry) => entry.state === "waiting" || entry.state === "installing");
}

function renderQueue() {
    const container = $("#download-list");
    if (!container) return;
    $("#download-count").textContent = tr("queueItems", {count: state.queue.length});
    const activeCount = state.queue.filter((entry) => entry.state === "waiting" || entry.state === "installing").length;
    const badge = $("#queue-badge");
    badge.hidden = activeCount === 0;
    badge.textContent = String(activeCount);
    container.replaceChildren();
    if (!state.queue.length) {
        const empty = document.createElement("div");
        empty.className = "empty-list";
        const title = document.createElement("strong");
        title.textContent = tr("queueEmpty");
        empty.append(title);
        container.append(empty);
        renderDetail();
        return;
    }
    for (const entry of state.queue) {
        const pkg = state.packages.find((candidate) => candidate.id === entry.package_id);
        const row = document.createElement("article");
        row.className = "data-row";
        const title = document.createElement("div");
        title.className = "row-title";
        const name = document.createElement("strong");
        name.textContent = pkg ? packageLabel(pkg) : entry.package_id;
        const message = document.createElement("span");
        message.className = "queue-message";
        message.textContent = entry.message || entry.package_id;
        title.append(name, message);
        const actions = document.createElement("div");
        actions.className = "row-actions";
        const status = document.createElement("span");
        status.className = `queue-state ${entry.state}`;
        status.textContent = tr(entry.state);
        actions.append(status);
        if (entry.state === "waiting") {
            const cancel = document.createElement("button");
            cancel.className = "secondary-button";
            cancel.type = "button";
            cancel.textContent = tr("cancelItem");
            cancel.addEventListener("click", async () => {
                await callApi("cancel_queue_item", entry.task_id);
                await pollQueue(true);
            });
            actions.append(cancel);
        } else if (entry.state === "failed" && entry.error_code === "file_conflict") {
            const force = document.createElement("button");
            force.className = "danger-button";
            force.type = "button";
            force.textContent = tr("forceRetry");
            force.addEventListener("click", async () => {
                const confirmed = await showModal({
                    kicker: tr("fileConflict"),
                    title: tr("forceConflictTitle"),
                    body: tr("forceConflictMessage"),
                    confirmText: tr("forceRetry"),
                    destructive: true,
                });
                if (!confirmed) return;
                const result = await callApi("enqueue_install", [entry.package_id], true, true);
                if (!result.ok) resultError(result);
                await pollQueue(true);
            });
            actions.append(force);
        } else if (entry.state === "failed") {
            const retry = document.createElement("button");
            retry.className = "secondary-button";
            retry.type = "button";
            retry.textContent = tr("retry");
            retry.addEventListener("click", async () => {
                retry.disabled = true;
                const result = await callApi("enqueue_install", [entry.package_id], true);
                if (!result.ok) resultError(result);
                await pollQueue(true);
            });
            actions.append(retry);
        }
        row.append(title, actions);
        container.append(row);
    }
    renderDetail();
}

async function pollQueue(force = false) {
    if (!state.ready) return;
    try {
        const result = await callApi("get_queue");
        if (!result.ok) return;
        const signature = JSON.stringify([result.entries, result.close_pending]);
        if (!force && signature === state.queueSignature) return;
        const previous = state.queueStates;
        state.queue = result.entries || [];
        state.queueSignature = signature;
        state.queueStates = new Map(state.queue.map((entry) => [entry.task_id, entry.state]));
        const newlyCompleted = state.queue.some((entry) => entry.state === "completed" && previous.get(entry.task_id) !== "completed");
        const newlyFailed = state.queue.find((entry) => entry.state === "failed" && previous.get(entry.task_id) !== "failed");
        renderQueue();
        renderMelonLoader();
        updatePageHeader();
        if (newlyCompleted) await refreshInstalled();
        if (newlyFailed) toast(newlyFailed.message || tr("operationFailed"), "error");
        if (result.close_pending) setStatus(tr("closeWaiting"));
    } catch (_error) {
        // A closing WebView can reject an in-flight poll. There is nothing left to update.
    }
}

async function reloadSettings() {
    const result = await callApi("get_settings");
    if (!result.ok) {
        resultError(result);
        return;
    }
    state.settings = result.settings;
    $("#debug-mode").checked = result.settings.debug;
    state.languageMode = result.settings.language;
    $("#language-select").value = state.languageMode;
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
    updatePageHeader();
}
