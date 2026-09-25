"use strict";

function renderDeveloperServers() {
    const container = $("#developer-server-list");
    if (!container) return;
    container.replaceChildren();
    for (const server of state.developerServers) {
        const row = document.createElement("article");
        row.className = "developer-server-row";

        const summary = document.createElement("div");
        summary.className = "developer-server-summary";
        const name = document.createElement("strong");
        name.textContent = server.name;
        const detail = document.createElement("span");
        const count = server.packages?.length ? ` · ${server.packages.length} ${tr("privateLabel")}` : "";
        const activeGrants = (server.entitlements?.grants || []).filter((grant) => grant.state === "active");
        let access = "";
        if (server.status === "offline") access = ` · ${tr("serverOffline")}`;
        else if (server.status === "reauth_required") access = ` · ${tr("reauthRequired")}`;
        else if (server.status === "expired") access = ` · ${tr("accessExpired")}`;
        else if (server.status === "revoked") access = ` · ${tr("accessRevoked")}`;
        else if (activeGrants.some((grant) => grant.expires_at == null)) access = ` · ${tr("accessPermanent")}`;
        else if (activeGrants.length) {
            const latest = Math.max(...activeGrants.map((grant) => Number(grant.expires_at) || 0));
            access = ` · ${tr("accessUntil", {time: new Date(latest * 1000).toLocaleString()})}`;
        }
        if (server.cached && server.synced_at) {
            access += ` · ${tr("cachedCatalog")} · ${tr("cachedAt", {time: new Date(server.synced_at * 1000).toLocaleString()})}`;
        }
        detail.textContent = `${server.url}${count}${access}`;
        summary.append(name, detail);

        const actions = document.createElement("div");
        actions.className = "developer-server-actions";
        if (server.status === "reauth_required") {
            const relogin = document.createElement("button");
            relogin.type = "button";
            relogin.className = "secondary-button";
            relogin.textContent = tr("login");
            relogin.addEventListener("click", startGithubDeviceLogin);
            actions.append(relogin);
        }
        const activate = document.createElement("button");
        activate.type = "button";
        activate.className = "primary-button";
        activate.textContent = tr("activate");
        activate.addEventListener("click", () => activateDeveloperServer(server.server_id));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary-button";
        remove.textContent = tr("removeServer");
        remove.addEventListener("click", () => removeDeveloperServer(server.server_id));
        actions.append(activate, remove);

        row.append(summary, actions);
        container.append(row);
    }
}

function renderGithubLogin() {
    const status = $("#github-login-state");
    if (!status) return;
    const loggedIn = Boolean(state.settings.github_user_id);
    status.textContent = loggedIn
        ? `${tr("loggedIn")} · ${state.settings.github_user_id}`
        : tr("loggedOut");
    status.classList.toggle("ready", loggedIn);
    const button = $("#github-login-button");
    if (button) {
        button.textContent = loggedIn ? tr("logout") : tr("login");
        button.dataset.authState = loggedIn ? "logged-in" : "logged-out";
    }
}

function handleGithubAuth() {
    return state.settings.github_user_id ? logoutGithub() : startGithubDeviceLogin();
}

async function startGithubDeviceLogin() {
    const result = await callApi("start_github_device_login");
    if (!result.ok) {
        if (result.code === "github_login_expired") {
            state.settings.github_user_id = "";
            renderGithubLogin();
            result = {...result, message: tr("githubLoginExpiredMessage")};
        }
        resultError(result);
        return;
    }
    const body = document.createElement("div");
    body.className = "github-device-modal-body";
    const instructions = document.createElement("p");
    instructions.textContent = tr("githubDeviceInstructions");
    const makeRow = (value, label, copiedMessage = "") => {
        const row = document.createElement("div");
        row.className = "github-device-value";
        const code = document.createElement("code");
        code.textContent = value;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary-button";
        button.textContent = label;
        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(value);
                if (copiedMessage) toast(copiedMessage);
            } catch (_error) { /* user can still select the visible value */
            }
        });
        row.append(code, button);
        return row;
    };
    body.append(
        instructions,
        makeRow(result.verification_uri, tr("copyVerificationUrl")),
        makeRow(result.user_code, tr("copyDeviceCode"), tr("deviceCodeCopied")),
    );
    try {
        await navigator.clipboard.writeText(result.user_code);
    } catch (_error) { /* clipboard permission is optional */
    }
    const dialog = showModal({
        kicker: tr("githubDeviceFlowKicker"),
        title: tr("githubLogin"),
        body,
        confirmText: tr("close"),
        cancelText: null,
        closeOnBackdrop: false,
    });
    const modalStatus = $("#modal-status");
    if (modalStatus) modalStatus.hidden = false;
    const deadline = Date.now() + Math.max(1, Number(result.expires_in || 900)) * 1000;
    let active = true;
    const formatRemaining = () => {
        const seconds = Math.ceil(Math.max(0, deadline - Date.now()) / 1000);
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    };
    const updateStatus = () => {
        if (modalStatus) modalStatus.textContent = tr("githubLoginWaiting", {time: formatRemaining()});
    };
    updateStatus();
    const countdown = window.setInterval(updateStatus, 1000);
    await callApi("open_url", result.verification_uri);
    const poll = async () => {
        if (!active || Date.now() >= deadline) {
            if (active) {
                active = false;
                closeModal(false);
                await callApi("cancel_github_device_login");
                toast(tr("githubLoginExpired"));
            }
            return;
        }
        const next = await callApi("poll_github_device_login");
        if (!active) return;
        if (!next.ok) {
            active = false;
            closeModal(false);
            resultError(next);
            return;
        }
        if (next.pending) {
            setTimeout(poll, Number(next.interval || result.interval || 5) * 1000);
            return;
        }
        active = false;
        closeModal(true);
        state.settings.github_user_id = next.github_user_id || "";
        renderGithubLogin();
        await loadDeveloperServers();
        if (Array.isArray(next.conflicts) && next.conflicts.length) {
            await reviewGistConflicts(next.conflicts);
        }
    };
    // Poll once immediately so an already-completed browser authorization is
    // reflected without making the user wait for the first interval.
    poll();
    const confirmed = await dialog;
    if (!confirmed && active) {
        active = false;
        await callApi("cancel_github_device_login");
    }
    window.clearInterval(countdown);
}

async function reviewGistConflicts(conflicts) {
    const body = document.createElement("div");
    body.className = "github-device-modal-body";
    const message = document.createElement("p");
    message.textContent = tr("gistConflictMessage");
    body.append(message);
    for (const conflict of conflicts) {
        const row = document.createElement("p");
        const local = conflict.local || {};
        const remote = conflict.remote || {};
        row.textContent = tr("gistConflictDetails", {
            server: conflict.server_id || "-",
            local: local.url || local.name || "-",
            remote: remote.url || remote.name || "-",
        });
        body.append(row);
    }
    const accept = await showModal({
        kicker: tr("githubGist"),
        title: tr("gistConflictTitle"),
        body,
        confirmText: tr("gistConflictAcceptRemote"),
        cancelText: tr("gistConflictKeepLocal"),
        closeOnBackdrop: false,
    });
    if (!accept) return;
    const result = await callApi(
        "resolve_github_gist_conflicts",
        conflicts.map((item) => item.server_id),
    );
    if (!result.ok) resultError(result);
    else await loadDeveloperServers();
}

async function logoutGithub() {
    const result = await callApi("logout_github");
    if (!result.ok) {
        resultError(result);
        return;
    }
    state.settings.github_user_id = "";
    renderGithubLogin();
    await loadDeveloperServers();
}

/** 让数据层刷一次开发者服务器：命令只回 ack，读数经推送回来（怎么画在 `business.js` 里）。 */
async function loadDeveloperServers() {
    if (!state.ready) return;
    try {
        const result = await callApi("data_request", "servers");
        if (!result.ok) resultError(result);
    } catch (error) {
        resultError({message: String(error)});
    }
}

async function addDeveloperServer() {
    const input = $("#developer-server-url");
    const url = input.value.trim();
    if (!url) return;
    let result = await callApi("add_developer_server", url);
    if (result.ok && result.requires_confirmation) {
        const identity = result.signing_identity || {};
        const transport = result.manual_transport ? ` (${result.manual_transport})` : "";
        const body = document.createElement("div");
        body.textContent = tr("serverTrustMessage", {
            name: result.server?.name || "",
            fingerprint: identity.fingerprint || "-",
            method: result.trust_method || "manual",
            transport,
        });
        const confirmed = await showModal({
            kicker: tr("serverTrustKicker"),
            title: tr("serverTrustTitle"),
            body,
            confirmText: tr("confirm"),
            cancelText: tr("cancel"),
        });
        if (!confirmed) return;
        result = await callApi("add_developer_server", url, identity.fingerprint || "");
    }
    if (!result.ok) {
        resultError(result);
        return;
    }
    input.value = "";
    toast(tr("serverAdded"));
    await loadDeveloperServers();
}

async function activateDeveloperServer(serverId) {
    if (!state.settings.github_user_id) {
        showMessage(tr("githubLoginRequired"));
        return;
    }
    const body = document.createElement("div");
    const keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.autocomplete = "off";
    keyInput.placeholder = tr("activationKey");
    body.append(keyInput);
    const confirmed = await showModal({
        kicker: tr("privateAccessKicker"),
        title: tr("enterActivationKey"),
        body,
        confirmText: tr("activate"),
    });
    const key = keyInput.value.trim();
    keyInput.value = "";
    if (!confirmed || !key) return;
    const result = await callApi("activate_developer_server", serverId, key);
    if (!result.ok) {
        resultError(result);
        return;
    }
    toast(tr("activationComplete"));
    await loadDeveloperServers();
}

async function removeDeveloperServer(serverId) {
    const result = await callApi("remove_developer_server", serverId);
    if (!result.ok) {
        resultError(result);
        return;
    }
    toast(tr("serverRemoved"));
    await loadDeveloperServers();
}
