// 在 Node 里执行真实的加载器页渲染逻辑（无浏览器）。
//
// 用法：node render_modloaders_harness.js <client_ui_dir> <payload.json>
//
// 加载仓库里未修改的 i18n/core/compatibility/modloaders 四个文件（合成一个脚本 —— 页面里
// 它们是分开的 `<script>`，但顶层 `const` 不跨脚本共享），调用 renderModloaders() 并输出
// 结构化结果，断言每张卡的名字、版本、状态芯片、动作按钮与依赖/推荐/提供三块。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_modloaders_harness.js <client_ui_dir> <payload.json>");
    process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
// 环境读数的序列：启动时先在没有注册表的情况下读一次，注册表随目录加载后再读一次。
const environmentSequence = Array.isArray(payload.environment_sequence) ? payload.environment_sequence : null;
let environmentReads = 0;

class FakeElement {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.className = "";
        this.disabled = false;
        this.type = "";
        this.hidden = false;
        this.value = "";
        this.title = "";
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
    "#modloader-list": new FakeElement("section"),
    "#modloader-count": new FakeElement("span"),
};
for (const selector of [
    "#environment-sprocket", "#environment-loaders", "#environment-note",
    "#status-text", ".status-mark", "#toast-region",
]) {
    elements[selector] = new FakeElement("div");
}
elements["#environment-install-loader"] = new FakeElement("button");

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
const toasts = [];
const api = {
    client_log: async () => ({ok: true}),
    get_modloaders: async () => {
        apiCalls.push({kind: "call", args: ["get_modloaders"]});
        return {ok: true, modloaders: payload.modloaders || []};
    },
    remove_modloader: async (id) => {
        apiCalls.push({kind: "call", args: ["remove_modloader", id]});
        const item = (payload.modloaders || []).find((entry) => entry.id === id) || {};
        return {ok: true, modloader: item};
    },
    get_environment: async (includeLatest) => {
        apiCalls.push({kind: "call", args: ["get_environment", includeLatest]});
        if (environmentSequence) {
            const entry = environmentSequence[Math.min(environmentReads, environmentSequence.length - 1)];
            environmentReads += 1;
            return {ok: true, ...entry};
        }
        return {ok: true, ...(payload.environment || {})};
    },
    open_url: async (url) => {
        apiCalls.push({kind: "call", args: ["open_url", url]});
        return {ok: true};
    },
};
const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    // 加载器安装走模组的安装路；这里记下页面是否把包交给了那条路，路由本身另有测试。
    beginInstall: (packageIds) => {
        apiCalls.push({kind: "call", args: ["beginInstall", ...packageIds]});
        return Promise.resolve(true);
    },
    // core.js 自己定义 callApi（走 window.pywebview.api），所以这里给的是一个假桥而不是同名桩。
    pywebview: {api},
    queueActive: () => Boolean(payload.queue_active),
    toast: (message, tone) => toasts.push({message, tone}),
    resultError: (result) => apiCalls.push({kind: "error", result}),
    renderStatusbar: () => {},
    renderGameState: () => {},
    showModal: async () => payload.confirm !== false,
    showPage: async () => {},
    // 环境轮询在环境变化时会重拉目录 / 刷新已装列表；这个 harness 只测左下角，两个都当空操作。
    loadCatalog: async () => true,
    refreshInstalled: async () => {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const injected = {
    ready: true,
    page: "modloaders",
    language: payload.language || "en",
    languageMode: payload.language || "en",
    packages: payload.packages || [],
    installed: [],
    localMods: [],
    queue: [],
    modloaders: payload.modloaders || [],
    modloadersRequested: true,
    environment: environmentSequence ? null : (payload.environment || null),
    environmentRevision: environmentSequence ? null : 1,
    environmentRenderKey: null,
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    "globalThis.__state = state;",
    fs.readFileSync(path.join(clientUiDir, "js", "compatibility.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "modloaders.js"), "utf8"),
].join("\n");

const context = vm.createContext(sandbox);
vm.runInContext(source, context, {filename: "modloaders.js"});

let error = null;
if (!environmentSequence) {
    try {
        vm.runInContext("renderModloaders(); renderEnvironment();", context);
    } catch (caught) {
        error = String((caught && caught.stack) || caught);
    }
}

function serialize(element) {
    if (!element || element.tagName === "#text") return null;
    return {
        tag: element.tagName,
        className: element.className,
        text: element.textContent,
        disabled: Boolean(element.disabled),
        children: element.children.map(serialize).filter(Boolean),
    };
}

const cards = elements["#modloader-list"].children.map(serialize);
const actions = ["install", "update", "reinstall", "remove"];
function actionButtons(index) {
    const card = elements["#modloader-list"].children[index];
    if (!card) return [];
    const found = [];
    (function walk(node) {
        if (!node || node.tagName === "#text") return;
        if (String(node.tagName).toLowerCase() === "button") {
            found.push({text: node.textContent, disabled: Boolean(node.disabled)});
        }
        for (const child of node.children || []) walk(child);
    })(card);
    return found;
}

async function exercise() {
    if (environmentSequence) {
        // 复现启动顺序：环境先读一次（没有注册表），目录加载完再读一次（注册表在场）。
        try {
            await vm.runInContext(
                "(async () => { await refreshEnvironment(true); await pollEnvironment(); })()",
                context,
            );
        } catch (caught) {
            error = String((caught && caught.stack) || caught);
        }
    }
    if (payload.click_primary !== undefined) {
        const card = elements["#modloader-list"].children[payload.click_primary];
        const button = card?.children?.[0]?.children?.[1]?.children?.[1];
        for (const handler of (button?.listeners?.click || [])) handler({});
    }
    if (payload.click_remove !== undefined) {
        const card = elements["#modloader-list"].children[payload.click_remove];
        const buttons = card?.children?.[0]?.children?.[1]?.children || [];
        const button = buttons[buttons.length - 1];
        for (const handler of (button?.listeners?.click || [])) handler({});
    }
    for (let attempt = 0; attempt < 12; attempt += 1) await new Promise((resolve) => setImmediate(resolve));
    return {
        error,
        count: elements["#modloader-count"].textContent,
        cards,
        buttons: cards.map((_card, index) => actionButtons(index)),
        apiCalls,
        toasts,
        sidebar: {
            loaders: elements["#environment-loaders"].children.map((line) => line.textContent),
            installHidden: Boolean(elements["#environment-install-loader"].hidden),
        },
    };
}

exercise().then((result) => {
    setImmediate(() => process.stdout.write(JSON.stringify(result), () => process.exit(0)));
});
