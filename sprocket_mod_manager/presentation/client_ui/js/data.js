"use strict";

// 前端只有"渲染镜像"：长期显示的数据归 Python 的数据层所有，只会由它推过来。
// 这一层**只写镜像、永不回写数据**；界面要改数据就发命令（`callApi("data_request", key, args)`），
// 命令只回 ack，改完的结果照旧经推送回来。
//
// 一个 key = 界面上一块长期显示的东西（例如 `installed` 是「已安装」页的一整份读数：
// 安装记录 + 磁盘扫描 + 未识别 + 摘要）。同一份数据在整页里只有这一处，别在页面里再留一份。

const DATA = {
    values: {},        // key -> 数据层推来的那一份
    revisions: {},     // key -> revision（旧包不回退）
    listeners: new Map(),  // key -> Set<handler>
};

/** 订一组 key：数据层写它们的时候会叫这个 handler（`handler(key, value)`）。 */
function dataWatch(keys, handler) {
    for (const key of keys) {
        if (!DATA.listeners.has(key)) DATA.listeners.set(key, new Set());
        DATA.listeners.get(key).add(handler);
    }
}

/** 数据层当前那一份（没有就是 undefined / null）。 */
function dataValue(key) {
    return DATA.values[key];
}

/** 数据层推来一次变更：写进镜像，然后**只**叫订阅了这个 key 的人。 */
function dataDeliver(event) {
    if (!event || typeof event.key !== "string") return;
    const revision = Number(event.revision || 0);
    const known = Number(DATA.revisions[event.key] || 0);
    if (revision && known && revision <= known) return;  // 迟到的旧包不许把新读数盖回去
    DATA.values[event.key] = event.value;
    DATA.revisions[event.key] = revision || known;
    for (const handler of DATA.listeners.get(event.key) || []) {
        try {
            handler(event.key, event.value);
        } catch (error) {
            reportClientLog("error", `data listener failed: ${event.key}: ${String(error)}`);
        }
    }
}

window.smmBridge = window.smmBridge || {};
window.smmBridge.deliver = dataDeliver;

/**
 * 把数据层的 key 接到既有的读取点上。
 *
 * 这些字段是**只读视图**：读的是数据层那一份，写只能由数据层推过来。谁还想着回写，
 * 这里会当场报出来（并忽略），不会被悄悄接受 —— 一份事实只有一处来源。
 */
function installDataMirror() {
    const views = {
        // 「已安装」页的一整份读数：磁盘扫描优先，安装记录只用来标注归属。
        installed: () => dataValue("installed")?.installed || [],
        unrecognized: () => dataValue("installed")?.unrecognized || [],
        localMods: () => dataValue("installed")?.local_mods || [],
        localSummary: () => dataValue("installed")?.local_summary || null,
        hasAnyMods: () => Boolean(dataValue("installed")?.has_any_mods),
        // 左下角与状态栏的那份环境读数（Sprocket 版本、加载器的在用版本、环境自洽判定）。
        environment: () => dataValue("environment") || null,
        // 安装队列那张表。
        queue: () => dataValue("queue")?.entries || [],
        // 加载器目录：注册表里有哪些加载器包、装没装、能不能更新、当前环境下能不能跑。
        modloaders: () => dataValue("loaders")?.modloaders || [],
        // 公开注册表那一份。
        publicPackages: () => dataValue("catalog")?.packages || [],
        // 开发者服务器：服务器清单与它们带来的私有包。
        developerServers: () => dataValue("servers")?.servers || [],
        privatePackages: () => dataValue("servers")?.packages || [],
        // 目录页看到的是「公开注册表 + 私有服务器」：合并只在这里发生一次，页面不再各自拼一遍。
        packages: () => [
            ...(dataValue("catalog")?.packages || []),
            ...(dataValue("servers")?.packages || []),
        ],
    };
    for (const [name, read] of Object.entries(views)) {
        Object.defineProperty(state, name, {
            configurable: true,
            enumerable: true,
            get: read,
            set: () => {
                reportClientLog("error", `refused to write data-layer field from the UI: ${name}`);
            },
        });
    }
}

/** 某个注册表包在本机的安装记录（数据层推来的那一份里查，不再往包对象上挂副本）。 */
function packageInstalled(pkg) {
    const id = pkg?.id;
    if (!id) return null;
    const records = dataValue("installed")?.installed || [];
    return records.find((entry) => entry.id === id) || null;
}

/** 订阅时拿回来的那份快照：和推送同一条路进镜像，免得初始化另开一条路。 */
function dataApplySnapshot(snapshot) {
    for (const [key, entry] of Object.entries(snapshot || {})) {
        if (!entry || !entry.known) continue;
        dataDeliver({key, value: entry.value, revision: entry.revision});
    }
}

installDataMirror();
