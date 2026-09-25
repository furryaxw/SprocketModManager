"use strict";

// 侧栏「上传日志」对话框：管理器日志恒在，加载器日志只在运行时真的在盘上时列出。
let logSources = [];

async function loadLogSources() {
    const result = await callApi("get_log_sources");
    if (!result.ok) {
        resultError(result);
        return logSources;
    }
    logSources = result.sources || [];
    return logSources;
}

function logSourceLabel(source) {
    if (source.kind === "manager") return tr("uploadManagerLog");
    return tr("logSourceLoader", {loader: source.loader});
}

/** 对话框正文：一行一个可用来源；点了哪一行就把那一项交回给 `openLogPicker`。 */
function logPickerBody() {
    const body = document.createElement("div");
    body.className = "log-picker-list";
    const available = logSources.filter((source) => source.available);
    for (const source of available) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "log-picker-item";
        item.textContent = logSourceLabel(source);
        item.addEventListener("click", () => closeModal(source));
        body.append(item);
    }
    if (!available.some((source) => source.kind === "loader")) {
        const empty = document.createElement("p");
        empty.className = "log-picker-empty";
        empty.textContent = tr("logNoLoader");
        body.append(empty);
    }
    return body;
}

/**
 * 侧栏按钮：先选来源，再走确认 → 上传 → 结果链接。
 *
 * 选完先关掉这个对话框，确认步骤才能起来——同一时刻只允许一个模态。
 */
async function openLogPicker() {
    await loadLogSources();
    const body = logPickerBody();
    const picked = showModal({
        kicker: tr("logUpload"),
        title: tr("logPickerTitle"),
        body,
        confirmText: tr("close"),
        cancelText: null,
    });
    const first = body.querySelector("button");
    if (first) window.setTimeout(() => first.focus(), 0);
    const source = await picked;
    if (!source || typeof source !== "object") return;
    await uploadLogSource(source);
}

async function uploadLogSource(source) {
    const confirmed = await showModal({
        kicker: tr("logUpload"),
        title: tr("uploadConfirmTitle"),
        body: source.kind === "manager"
            ? tr("uploadManagerLogConfirmMessage")
            : tr("uploadLoaderLogConfirmMessage", {loader: source.loader}),
        confirmText: tr("confirm"),
        closeOnBackdrop: false,
    });
    if (!confirmed) return;
    const result = await callApi("upload_log", source.id);
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
}
