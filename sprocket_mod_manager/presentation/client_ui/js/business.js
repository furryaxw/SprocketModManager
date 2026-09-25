"use strict";

// 业务层：长期读数的**监听**都写在这一处 —— 哪个 key 变了、该重画哪块、要不要顺手做点什么。
//
// 显示层（各页面文件里的 `renderXxx`）只负责画；用户操作只负责发命令（`callApi("data_request", …)`）。
// 订阅与"变了之后干什么"放在一起，是为了同一份读数只有一条反应路径：以前每个页面各自订阅、
// 各自记一份派生结果，谁先谁后就决定了界面上显示什么。
//
// 这份名单是"我显示什么"，所以留在界面这层；key 本身归数据层（`application/data_hub.py`）。

const DATA_KEYS = ["installed", "environment", "queue", "loaders", "catalog", "servers"];

/** 已安装页那一整份读数变了。 */
function watchInstalledData() {
    dataWatch(["installed"], () => {
        if (!state.ready) return;
        renderInstalled();
        renderCatalog();
        renderStatusbar();
    });
}

/**
 * 环境读数变了：左下角与状态栏重画，判定口径变了才值得重拉目录。
 */
function watchEnvironmentData() {
    dataWatch(["environment"], () => {
        if (!state.ready) return;
        const result = state.environment;
        const axes = environmentKey(result);
        renderStatusbar();
        renderEnvironment();
        if (axes !== state.environmentAxesKey) {
            state.environmentAxesKey = axes;
            void loadCatalog(false);
        }
        void ensureModloaderNames();
    });
}

/**
 * 队列那张表变了。
 *
 * 数据层定时问一次队列（便宜）、**只在表真的变了**时推送；这边的比对只剩"这条错报过没有"，
 * 那是界面状态，不是数据。
 */
function watchQueueData() {
    dataWatch(["queue"], () => {
        if (!state.ready) return;
        const payload = dataValue("queue") || {};
        const entries = payload.entries || [];
        renderQueue();
        renderModloaders();
        updatePageHeader();
        // 队列跑没跑完也是状态栏要看的活状态。
        renderStatusbar();
        // 队列里失败的那一条也算「出过事」：状态栏红着，直到下一次操作成功；同一个任务只报一次。
        const failed = entries.find(
            (entry) => entry.state === "failed" && !state.failedQueueTasks.has(entry.task_id),
        );
        if (failed) {
            state.failedQueueTasks.add(failed.task_id);
            setStatus(failed.message || tr("operationFailed"), "error");
        }
        if (payload.close_pending) setStatus(tr("closeWaiting"));
    });
}

/** 加载器目录变了。 */
function watchLoadersData() {
    dataWatch(["loaders"], () => {
        if (!state.ready) return;
        renderModloaders();
        renderEnvironment();
        updatePageHeader();
    });
}

/** 注册表目录变了：收尾这次加载的界面状态，并顺手认领磁盘上已有的模组。 */
function watchCatalogData() {
    dataWatch(["catalog"], () => {
        if (!state.ready) return;
        state.catalogLoading = false;
        state.batch.clear();
        if (!state.packages.some((pkg) => pkg.id === state.selectedId)) state.selectedId = null;
        setRegistryState("ready", tr("connected"));
        setStatus(
            tr("ready", {count: state.packages.length}),
            "ready",
            dataValue("catalog")?.source || "",
        );
        renderCatalog();
        renderInstalled();
        // 目录回来了才谈得上认领：磁盘上已有的模组要能对上注册表里的包。
        void claimExistingMods();
    });
}

/** 开发者服务器那份读数变了：GitHub 登录状态跟着它一起来，所以这里也顺手把登录行重画。 */
function watchServersData() {
    dataWatch(["servers"], () => {
        if (!state.ready) return;
        const payload = dataValue("servers") || {};
        if (payload.github_login_expired) {
            state.settings.github_user_id = "";
            renderGithubLogin();
        } else if (typeof payload.github_user_id === "string") {
            state.settings.github_user_id = payload.github_user_id;
            renderGithubLogin();
        }
        renderDeveloperServers();
        renderCatalog();
    });
}

/** 把界面长期显示的那些读数全部订阅上（启动时调一次）。 */
function watchData() {
    watchInstalledData();
    watchEnvironmentData();
    watchQueueData();
    watchLoadersData();
    watchCatalogData();
    watchServersData();
}
