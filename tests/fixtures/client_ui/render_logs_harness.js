// 在 Node 里执行真实的侧栏日志来源对话框逻辑（无浏览器）。
//
// 用法：node render_logs_harness.js <client_ui_dir> <payload.json>
//
// 加载仓库里未修改的 i18n/core/logs 三个文件（合成一个脚本 —— 页面里它们是分开的
// `<script>`，但顶层 `const` 不跨脚本共享），注入 `get_log_sources` / `upload_log` 的假桥，
// 以及一组能真的把选择结果交回去的 `showModal` / `closeModal`，输出对话框内容、模态序列、
// API 调用与 toast：断言只有可用的来源才列出来、选了哪一项就上传那一项、确认步骤排在
// 来源对话框之后。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_logs_harness.js <client_ui_dir> <payload.json>");
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
        this.value = "";
        this.readOnly = false;
        this.listeners = {};
        this.attributes = {};
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

    focus() {
        this.focused = true;
    }

    select() {
        this.selected = true;
    }

    remove() {
        this.removed = true;
    }

    querySelector() {
        return null;
    }

    get classList() {
        const owner = this;
        return {
            add: (name) => {
                if (!owner.className.split(" ").includes(name)) owner.className = `${owner.className} ${name}`.trim();
            },
            remove: (name) => {
                owner.className = owner.className.replace(name, "").trim();
            },
            toggle: (name, force) => {
                const has = owner.className.split(" ").includes(name);
                const wanted = force === undefined ? !has : Boolean(force);
                if (wanted && !has) owner.className = `${owner.className} ${name}`.trim();
                if (!wanted && has) owner.className = owner.className.replace(name, "").trim();
            },
        };
    }
}

const elements = {
    "#upload-logs": new FakeElement("button"),
    "#modal-cancel": new FakeElement("button"),
    "#toast-region": new FakeElement("div"),
};

const documentStub = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => {
        const node = new FakeElement("#text");
        node.textContent = text;
        return node;
    },
    querySelector: (selector) => elements[selector] || null,
    querySelectorAll: () => [],
};

function settle(rounds = 12) {
    return Array.from({length: rounds}).reduce(
        (chain) => chain.then(() => new Promise((resolve) => setImmediate(resolve))),
        Promise.resolve(),
    );
}

const apiCalls = [];
const toasts = [];
const modals = [];
let pendingResolve = null;

function showModal(options) {
    modals.push(options);
    return new Promise((resolve) => {
        pendingResolve = resolve;
    });
}

function closeModal(result = false) {
    const resolve = pendingResolve;
    pendingResolve = null;
    if (resolve) resolve(result);
}

const api = {
    client_log: async () => ({ok: true}),
    get_log_sources: async () => {
        apiCalls.push({kind: "call", args: ["get_log_sources"]});
        return {ok: true, sources: payload.sources || []};
    },
    upload_log: async (sourceId) => {
        apiCalls.push({kind: "call", args: ["upload_log", sourceId]});
        if (payload.upload_ok === false) {
            return {ok: false, code: "log_source_unavailable", message: "nope"};
        }
        return {ok: true, url: "https://paste.furryaxw.top/@/anonymous/example"};
    },
};
const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    pywebview: {api},
    navigator: {clipboard: {writeText: async () => {}}},
    toast: (message, tone) => toasts.push({message, tone}),
    showModal,
    closeModal,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const injected = {
    ready: true,
    page: "installed",
    language: payload.language || "en",
    languageMode: payload.language || "en",
    settings: {},
    links: {},
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "data.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    fs.readFileSync(path.join(clientUiDir, "js", "logs.js"), "utf8"),
].join("\n");

const context = vm.createContext(sandbox);
let error = null;
try {
    vm.runInContext(source, context, {filename: "logs.js"});
} catch (caught) {
    error = String((caught && caught.stack) || caught);
}

function pickerItems(body) {
    return body.children.filter((node) => node.className === "log-picker-item");
}

function serialize(node) {
    return {className: node.className, text: node.textContent};
}

async function exercise() {
    try {
        // 对话框要等人点，所以这里不等它的返回。
        vm.runInContext("openLogPicker()", context);
    } catch (caught) {
        error = error || String((caught && caught.stack) || caught);
    }
    await settle();

    const picker = modals[0];
    const pickerBody = picker ? picker.body : new FakeElement("div");
    const items = pickerItems(pickerBody);

    const clicked = payload.click;
    if (clicked !== undefined && clicked !== null) {
        for (const handler of (items[clicked]?.listeners?.click || [])) handler({});
    }
    await settle();

    // 来源选完之后是确认对话框；按 payload.confirm 回答它。
    if (modals.length > 1 && pendingResolve) closeModal(payload.confirm !== false);
    await settle();

    return {
        error,
        pickerTitle: picker ? picker.title : null,
        pickerCancel: picker && "cancelText" in picker ? picker.cancelText : null,
        entries: pickerBody.children.map(serialize),
        items: items.map((node) => node.textContent),
        emptyShown: pickerBody.children.some((node) => node.className === "log-picker-empty"),
        modals: modals.map((options) => options.title),
        apiCalls,
        toasts,
    };
}

exercise().then((result) => {
    setImmediate(() => process.stdout.write(JSON.stringify(result), () => process.exit(0)));
});
