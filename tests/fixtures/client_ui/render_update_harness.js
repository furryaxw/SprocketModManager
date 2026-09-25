// 在 Node 里执行真实 modloaders.js 的启动更新检查（无浏览器）。
//
// 用法：node render_update_harness.js <client_ui_dir> <payload.json>
//
// 目的：把「启动时发现新版本」那条路跑起来并断言到底发了哪些调用 —— 打包版应该走
// `apply_manager_update`（下载并换壳），源码运行只能 `open_url` 去发布页；选「稍后」
// 则两个都不发、直接继续用。这不是打包 WebView 的验收，但能抓住接线错误。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_update_harness.js <client_ui_dir> <payload.json>");
    process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

class FakeElement {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.className = "";
        this.textContent = "";
        this.title = "";
        this.value = "";
        this.hidden = false;
        this.disabled = false;
        this.listeners = {};
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

    remove() {
        this.removed = true;
        if (this.parent?.children) {
            this.parent.children = this.parent.children.filter((child) => child !== this);
        }
    }

    setAttribute() {}

    querySelector() {
        return null;
    }

    get classList() {
        const owner = this;
        return {
            add: (name) => { owner.className = `${owner.className} ${name}`.trim(); },
            remove: (name) => { owner.className = owner.className.replace(name, "").trim(); },
            toggle: (name, force) => {
                const has = owner.className.split(" ").includes(name);
                const wanted = force === undefined ? !has : Boolean(force);
                if (wanted && !has) owner.className = `${owner.className} ${name}`.trim();
                if (!wanted && has) owner.className = owner.className.replace(name, "").trim();
            },
        };
    }
}

const elements = {};
for (const selector of [
    "#manager-update", "#latest-version", "#modal-status", "#modal-cancel",
    "#status-text", ".status-mark", "#toast-region", "#modal-layer",
]) {
    elements[selector] = new FakeElement("div");
}
elements["#modal-layer"].hidden = true;

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

const apiCalls = [];
let modalOptions = null;
let resolveModal = null;

// core.js / modloaders.js 自己带 `tr`/`toast`/`setStatus`/`resultError`，这里只补它们没有的：
// 弹窗（dialogs.js 不加载）与假桥。
const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    showModal: (options) => {
        modalOptions = options;
        return new Promise((resolve) => { resolveModal = resolve; });
    },
    closeModal: (result) => {
        if (resolveModal) resolveModal(Boolean(result));
        resolveModal = null;
    },
};

const injected = {
    ready: true,
    language: "en",
    languageMode: "en",
    page: "about",
    update: null,
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "data.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    fs.readFileSync(path.join(clientUiDir, "js", "modloaders.js"), "utf8"),
].join("\n");

sandbox.window = sandbox;
sandbox.globalThis = sandbox;

sandbox.pywebview = {
    api: {
        client_log: async () => ({ok: true}),
        get_manager_update: async () => {
            apiCalls.push({kind: "call", args: ["get_manager_update"]});
            return payload.update || {ok: false, code: "update_check_failed", message: "nope"};
        },
        apply_manager_update: async () => {
            apiCalls.push({kind: "call", args: ["apply_manager_update"]});
            return payload.apply || {ok: true, version: "9.9.9", staged: "x"};
        },
        open_url: async (url) => {
            apiCalls.push({kind: "call", args: ["open_url", url]});
            return {ok: true};
        },
    },
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: "update.js"});

function bodyText(node) {
    if (!node) return "";
    const own = typeof node.textContent === "string" ? node.textContent : "";
    const children = (node.children || []).map(bodyText);
    return [own, ...children].filter(Boolean).join("\n");
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function main() {
    const started = vm.runInContext(
        `checkManagerUpdate(${payload.startup === false ? "false" : "true"});`,
        sandbox,
    );
    for (let attempt = 0; attempt < 10 && !modalOptions; attempt += 1) await tick();
    const modal = modalOptions
        ? {
            title: modalOptions.title,
            confirmText: modalOptions.confirmText,
            cancelText: modalOptions.cancelText,
            body: bodyText(modalOptions.body),
        }
        : null;
    if (modalOptions) {
        // 「稍后」＝resolve false；确认＝resolve true。
        resolveModal(payload.confirm !== false);
        resolveModal = null;
    }
    for (let attempt = 0; attempt < 10; attempt += 1) await tick();
    await started.catch(() => {});
    return {
        modal,
        apiCalls,
        toasts: elements["#toast-region"].children.map((node) => node.textContent),
        button: {
            text: elements["#manager-update"].textContent,
            disabled: Boolean(elements["#manager-update"].disabled),
        },
        latest: elements["#latest-version"].textContent,
        status: elements["#modal-status"].textContent,
    };
}

main()
    .then((result) => {
        setImmediate(() => process.stdout.write(JSON.stringify(result), () => process.exit(0)));
    })
    .catch((error) => {
        setImmediate(() => process.stdout.write(JSON.stringify({error: String(error && error.stack || error)}), () => process.exit(0)));
    });
