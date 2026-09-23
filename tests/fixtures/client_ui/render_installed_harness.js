// 在 Node 里执行真实的 installs.js 渲染逻辑（无浏览器）。
//
// 用法：node render_installed_harness.js <client_ui_dir> <payload.json>
//
// 目的：GUI 的「已安装」页在无头环境下也能被真正执行与断言——注入最小 DOM，加载仓库里
// 未修改的 installs.js，调用 renderInstalled() 并输出结构化结果。这不是打包 WebView 的
// 验收，但能抓住"字段接错/按钮没接线/芯片缺失"这类问题。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_installed_harness.js <client_ui_dir> <payload.json>");
    process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

class FakeElement {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.className = "";
        this.disabled = false;
        this.type = "";
        this.hidden = false;
        this.listeners = {};
        this.attributes = {};
        this.title = "";
        this._text = "";
    }

    get textContent() {
        return this._text;
    }

    set textContent(value) {
        this._text = value === undefined || value === null ? "" : String(value);
    }

    append(...nodes) {
        for (const node of nodes) this.children.push(node);
    }

    replaceChildren(...nodes) {
        this.children = nodes.slice();
    }

    addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    querySelector() {
        return null;
    }

    get classList() {
        const owner = this;
        return {
            add: (name) => { owner.className = `${owner.className} ${name}`.trim(); },
            remove: (name) => { owner.className = owner.className.replace(name, "").trim(); },
        };
    }
}

const elements = {
    "#installed-list": new FakeElement("div"),
    "#installed-count": new FakeElement("span"),
    "#installed-filter-all": new FakeElement("button"),
    "#installed-filter-enabled": new FakeElement("button"),
    "#installed-filter-disabled": new FakeElement("button"),
    "#installed-filter-outdated": new FakeElement("button"),
    "#installed-selection": new FakeElement("span"),
    "#installed-selection-bar": new FakeElement("div"),
    "#toggle-selection": new FakeElement("button"),
    "#invert-selection": new FakeElement("button"),
    "#update-selected": new FakeElement("button"),
    "#disable-selected": new FakeElement("button"),
    "#enable-selected": new FakeElement("button"),
    "#remove-selected": new FakeElement("button"),
};

const documentStub = {
    createElement: (tag) => new FakeElement(tag),
    getElementById: (id) => elements[`#${id}`] || null,
    querySelector: (selector) => elements[selector] || null,
    createTextNode: (text) => {
        const node = new FakeElement("#text");
        node.textContent = text;
        return node;
    },
};

const apiCalls = [];
const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    state: {
        ready: true,
        language: "zh",
        languageMode: "zh",
        packages: payload.packages || [],
        installed: payload.installed || [],
        unrecognized: payload.unrecognized || [],
        localMods: payload.local_mods || [],
        localSummary: payload.local_summary || null,
        queue: [],
        batch: new Map(),
        installedFilter: payload.filter || "all",
        installedSelection: new Set(),
        environment: payload.environment || null,
    },
    localized: (values, fallback = "") => {
        if (!values || typeof values !== "object") return fallback;
        const entries = Object.entries(values);
        if (!entries.length) return fallback;
        const language = sandbox.state.language;
        const exact = entries.find(([key]) => key.toLowerCase() === language.toLowerCase());
        if (exact) return exact[1];
        const base = language.split("-", 1)[0].toLowerCase();
        const related = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === base);
        if (related) return related[1];
        const english = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === "en");
        return english ? english[1] : entries[0][1];
    },
    $: (selector) => elements[selector] || null,
    $$: () => [],
    // 与 core.js 同一套版本比较：更新提示靠它判断「发布版本比已装版本新」。
    semverParts: (value) => {
        const match = String(value || "").match(/^(\d+)\.(\d+)\.(\d+)(?:-(.*))?$/);
        return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] || ""] : [0, 0, 0, ""];
    },
    compareVersions: (left, right) => {
        const a = sandbox.semverParts(left);
        const b = sandbox.semverParts(right);
        for (let index = 0; index < 3; index += 1) {
            if (a[index] !== b[index]) return a[index] - b[index];
        }
        if (a[3] === b[3]) return 0;
        if (!a[3]) return 1;
        if (!b[3]) return -1;
        return String(a[3]).localeCompare(String(b[3]));
    },
    tr: (key, values = {}) => {
        // 只翻译本 harness 断言的键；其余返回键名，便于发现未覆盖的文案。
        const table = {
            detectedMods: `${values.count} detected mods`,
            missingDepsCount: `${values.count} missing deps`,
            noInstalled: "No mods detected",
            unrecognized: "Unrecognized",
            requested: "User-installed",
            dependency: "Installed dependency",
            enableMod: "Enable",
            disableMod: "Disable",
            corrupted: "Corrupted",
            suppressCorruption: "Mute warning",
            unsuppressCorruption: "Unmute warning",
            integritySuppressed: "Muted the integrity warning for {{name}}",
            integrityUnsuppressed: "Restored the integrity warning for {{name}}",
            reinstall: "Reinstall",
            reinstallUnavailable: "This package is not in the registry; cannot reinstall automatically",
            remove: "Remove",
            requiresLabel: "Requires",
            missingLabel: "Missing",
            incompatibleLabel: "Incompatible",
            localOnly: "Local only",
            selectRow: "Select",
            selectAll: "Select all",
            clearSelection: "Clear selection",
            invertSelection: "Invert selection",
            selectionCount: `${values.count} selected`,
            installedFilterAll: "All",
            installedFilterEnabled: "Enabled",
            installedFilterDisabled: "Disabled",
            installedFilterOutdated: "Updates",
            noFilteredMods: "No mods match this filter",
            selectionNotApplicable: "The selection does not support this action",
            newVersionAvailable: `Version ${values.version} available`,
            noUpdates: "No updates available",
            updateQueued: `Queued ${values.count} update task(s)`,
            batchEnabled: `Enabled ${values.count} mod(s)`,
            batchDisabled: `Disabled ${values.count} mod(s)`,
            batchRemoved: `Removed ${values.count} mod(s)`,
            batchPartial: `${values.done} succeeded, ${values.failed} failed`,
            confirmBatchRemove: "Confirm removal",
            removeSelectedMessage: `Remove the ${values.count} selected mod(s)?`,
            incompatibleUpdateHead: `Update to ${values.version} is available, but it does not support your `,
            incompatibleUpdateUnknown: `Update to ${values.version} is available, but this environment cannot run it`,
            environmentAxisSprocket: `Sprocket ${values.version}`,
            environmentAxisMelonLoader: `MelonLoader ${values.version}`,
            environmentAxisAnd: " and ",
        };
        return table[key] !== undefined ? table[key] : key;
    },
    packageLabel: (pkg) => (pkg && pkg.name) || "",
    focusPackage: async (packageId) => {
        apiCalls.push({kind: "focus", id: packageId});
        return true;
    },
    queueActive: () => false,
    toast: () => {},
    setStatus: () => {},
    resultError: (result) => { apiCalls.push({ kind: "error", result }); },
    renderCatalog: () => {},
    renderDetail: () => {},
    showPage: async () => {},
    pollQueue: async () => {},
    showModal: async () => true,
    ensureMelonLoader: async () => ({ proceed: true, allowWithout: false }),
    updatePageHeader: () => {},
    renderGithubLogin: () => {},
    applyTextScale: () => {},
    syncProxyControls: () => {},
    callApi: async (...args) => {
        apiCalls.push({ kind: "call", args });
        if (args[0] === "toggle_mod") return { ok: true, toggled: args[1], restart_required: true };
        // 刷新必须回同样的 payload，否则会掩盖"操作后列表被清空"这类问题。
        if (args[0] === "get_installed") return { ok: true, ...payload };
        return { ok: true };
    },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const context = vm.createContext(sandbox);
// `compatibility.js` 与 `installs.js` 合成一个脚本再执行：页面上它们是分开的两个
// `<script>`，但顶层的 `const`（三色常量）不跨脚本共享，合成后才与页面里的可见性一致。
const source = ["compatibility.js", "installs.js"]
    .map((name) => fs.readFileSync(path.join(clientUiDir, "js", name), "utf8"))
    .join("\n");
vm.runInContext(source, context, { filename: "installs.js" });

for (const key of payload.selection || []) sandbox.state.installedSelection.add(String(key));
vm.runInContext("renderInstalled();", context);

// 行上三种事件各走一遍：单击（不做事）、双击（跳转）、右键（多选）。
// `target` 为空表示点在行体上；`closest` 命中表示点在行内控件上，行不该接管。
function dispatchRow(index, type, event) {
    const row = elements["#installed-list"].children[index];
    for (const handler of (row?.listeners?.[type] || [])) handler(event);
}
for (const index of payload.clickRows || []) dispatchRow(index, "click", {target: null});
for (const index of payload.dblclickRows || []) dispatchRow(index, "dblclick", {target: null});
for (const index of payload.contextRows || []) {
    dispatchRow(index, "contextmenu", {target: null, preventDefault: () => {}});
}
for (const index of payload.dblclickRowButtons || []) {
    dispatchRow(index, "dblclick", {target: {closest: () => ({tagName: "BUTTON"})}});
}

function serialize(element) {
    if (!element || element.tagName === "#text") return null;
    return {
        tag: element.tagName,
        className: element.className,
        text: element.textContent,
        title: element.title || "",
        ariaLabel: element.attributes["aria-label"] || "",
        disabled: Boolean(element.disabled),
        checked: Boolean(element.checked),
        clickable: Object.keys(element.listeners).length > 0,
        children: element.children.map(serialize).filter(Boolean),
    };
}

const rows = elements["#installed-list"].children.map(serialize);

// 触发启用/禁用与抑制按钮，验证它们真的带着正确路径与目标状态调用后端。
function collectButtons(element, found = []) {
    if (!element || element.tagName === "#text") return found;
    if (element.tagName === "button" && Object.keys(element.listeners).length > 0) found.push(element);
    for (const child of element.children || []) collectButtons(child, found);
    return found;
}

const CLICKABLE = new Set(["Enable", "Disable", "Mute warning", "Unmute warning"]);
const clicks = [];
// 批量操作单独跑时不点行内按钮：否则行内点击的 API 调用会和批量操作的混在一起。
if (!payload.action) {
    for (const button of collectButtons(elements["#installed-list"])) {
        if (!CLICKABLE.has(button.textContent)) continue;
        clicks.push(button.textContent);
        for (const handler of button.listeners.click || []) handler({});
    }
}

// 批量操作按 payload.action 真实跑一遍：工具栏按钮的接线在 main.js 里，harness 不加载它，
// 所以直接调用 installs.js 里的入口函数，核对它们发出的 API 调用。
const ACTIONS = {
    "update-selected": "void updateSelectedMods();",
    "disable-selected": "void toggleSelectedMods(false);",
    "enable-selected": "void toggleSelectedMods(true);",
    "remove-selected": "void removeSelectedMods();",
    "toggle-selection": "toggleInstalledSelection();",
    "invert-selection": "invertInstalledSelection();",
};
if (ACTIONS[payload.action]) vm.runInContext(ACTIONS[payload.action], context);

setImmediate(() => {
    process.stdout.write(JSON.stringify({
        count: elements["#installed-count"].textContent,
        rows,
        clickedButtons: clicks,
        apiCalls,
        toolbar: {
            selection: elements["#installed-selection"].textContent,
            barHidden: Boolean(elements["#installed-selection-bar"].hidden),
            toggle: {
                text: elements["#toggle-selection"].textContent,
                disabled: Boolean(elements["#toggle-selection"].disabled),
            },
            invertDisabled: Boolean(elements["#invert-selection"].disabled),
            filters: Object.fromEntries(
                ["all", "enabled", "disabled", "outdated"].map((key) => [
                    key,
                    {
                        text: elements[`#installed-filter-${key}`].textContent,
                        active: elements[`#installed-filter-${key}`].className.includes("active"),
                    },
                ]),
            ),
            buttons: Object.fromEntries(
                ["#update-selected", "#disable-selected", "#enable-selected", "#remove-selected"]
                    .map((selector) => [selector.replace("#", ""), Boolean(elements[selector].disabled)]),
            ),
        },
    }));
});
