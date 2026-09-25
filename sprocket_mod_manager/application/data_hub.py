"""客户端的数据层：长期显示的数据在这里只存一份，按 key 读写、按 key 通知。

只有这一层碰 I/O：启动时全量拉一次，之后按周期与文件变动刷新，写操作之后再把受影响的 key 刷一遍。
每次写进某个 key 都让它的 `revision` +1，并把新值推给**订阅了这个 key** 的界面 —— 没订阅的人不会被惊动。

放在 `application/` 是因为它要用 infrastructure 的适配器读盘/拉索引，而分层规则不许 `application`
依赖 `presentation`（见 `tests/test_module_boundaries.py`）：推送走**注册进来的回调**，数据层自己不认识窗口。

读写分寸（"禁止请求后返回"）：`get()` 是读，返回值就是数据；`request()` 是**刷新命令**，只回 ack，
数据一律经 `publish()` 广播出去。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Mapping

LOGGER = logging.getLogger(__name__)

# 数据层持有的键：长期显示的东西都在这里，一处一份。
# 一个键 = 界面上一块长期显示的东西（比如「已安装」页的一整份读数），而不是一个字段。
# 文案（i18n）不属于这一层；这里只放"有什么"。
KEY_CATALOG = "catalog"
KEY_INSTALLED = "installed"
KEY_LOADERS = "loaders"
KEY_ENVIRONMENT = "environment"
KEY_QUEUE = "queue"
KEY_SERVERS = "servers"

KEYS: tuple[str, ...] = (
    KEY_CATALOG,
    KEY_INSTALLED,
    KEY_LOADERS,
    KEY_ENVIRONMENT,
    KEY_QUEUE,
    KEY_SERVERS,
)

# 一次刷新：参数来自 `request()`，返回值就是要写进这个 key 的东西。
Refresher = Callable[..., Any]
# 推送：数据层 → 界面。由表现层注册，数据层不关心对面是窗口还是测试夹具。
Pusher = Callable[[dict[str, Any]], None]


class DataHub:
    """数据层的门面：唯一存储 + 订阅表 + 刷新队列。

    订阅按 key 记名（同一个界面可以只订它真正显示的那几个 key）。写入只在**值真的变了**时广播，
    所以重复刷新不会让界面白重画。
    """

    def __init__(self, *, pusher: Pusher | None = None) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._revisions: dict[str, int] = {}
        self._subscribers: dict[str, set[str]] = {}
        self._known: set[str] = set()
        self._refreshers: dict[str, Refresher] = {}
        self._pusher: Pusher | None = pusher
        self._pending: dict[str, tuple[int, Mapping[str, Any]]] = {}
        self._epoch = 0
        self._worker: threading.Thread | None = None
        self._tasks: list[threading.Thread] = []
        self._stopped = threading.Event()
        self._wake = threading.Event()
        self._closing = False

    # ---- 表现层接进来的东西 -------------------------------------------------

    def set_pusher(self, pusher: Pusher | None) -> None:
        with self._lock:
            self._pusher = pusher

    def register(self, key: str, refresher: Refresher) -> None:
        """给某个 key 挂上刷新器：只有数据层知道怎么把它算出来。"""
        with self._lock:
            self._refreshers[key] = refresher

    # ---- 读 -----------------------------------------------------------------

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._known

    def get(self, key: str) -> Any:
        """同步读当前那一份（内存，不触发任何 I/O）。没有值就是 None。"""
        with self._lock:
            return self._values.get(key)

    def revision(self, key: str) -> int:
        with self._lock:
            return self._revisions.get(key, 0)

    def snapshot(self, keys: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
        """按 key 取「值 + revision」，用于首次渲染与诊断。"""
        wanted = list(KEYS if keys is None else keys)
        with self._lock:
            return {
                key: {
                    "revision": self._revisions.get(key, 0),
                    "value": self._values.get(key),
                    "known": key in self._known,
                }
                for key in wanted
            }

    # ---- 订阅 ---------------------------------------------------------------

    def subscribe(self, subscriber: str, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        """声明关心哪些 key，并把当前的快照交回去（监听建立前的那一份）。"""
        with self._lock:
            for key in keys:
                self._subscribers.setdefault(str(key), set()).add(str(subscriber))
        LOGGER.debug("data hub subscribe subscriber=%s keys=%s", subscriber, list(keys))
        return self.snapshot(keys)

    def unsubscribe(self, subscriber: str, keys: Iterable[str] | None = None) -> None:
        with self._lock:
            targets = list(keys) if keys is not None else list(self._subscribers)
            for key in targets:
                listeners = self._subscribers.get(key)
                if not listeners:
                    continue
                listeners.discard(str(subscriber))
                if not listeners:
                    del self._subscribers[key]

    def subscribers(self, key: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._subscribers.get(key, ()))

    # ---- 写 ----------------------------------------------------------------

    def publish(self, key: str, value: Any) -> bool:
        """写入一个 key：值变了才算一次变更 —— revision +1 并推给所有订阅它的人。"""
        with self._lock:
            if key in self._known and self._values.get(key) == value:
                return False
            self._values[key] = value
            self._known.add(key)
            self._revisions[key] = self._revisions.get(key, 0) + 1
            revision = self._revisions[key]
            pusher = self._pusher
        self._notify(key, value, revision, pusher)
        return True

    def invalidate(self, keys: Iterable[str]) -> list[str]:
        """作废某些 key（例如换了游戏目录）：值清空、revision 前进、订阅者收到空值。

        作废同时把刷新世代往前推：**作废之前发起的刷新，结果一律丢掉**。否则换目录时那次还在路上的
        旧目录读数会在作废之后回来，把上一个目录的数据又推上界面。
        """
        with self._lock:
            self._epoch += 1
            dropped: list[str] = []
            events: list[tuple[str, int]] = []
            for raw in keys:
                key = str(raw)
                self._values.pop(key, None)
                self._known.discard(key)
                self._revisions[key] = self._revisions.get(key, 0) + 1
                events.append((key, self._revisions[key]))
                dropped.append(key)
            pusher = self._pusher
        for key, revision in events:
            self._notify(key, None, revision, pusher)
        LOGGER.info("data hub invalidated keys=%s", dropped)
        return dropped

    def _notify(self, key: str, value: Any, revision: int, pusher: Pusher | None) -> None:
        with self._lock:
            listeners = sorted(self._subscribers.get(key, ()))
        LOGGER.debug("data hub publish key=%s revision=%d listeners=%d", key, revision, len(listeners))
        if pusher is None or not listeners:
            return
        try:
            pusher({"key": key, "value": value, "revision": revision})
        except Exception:  # noqa: BLE001 - 推不出去不能让数据层自己崩掉
            LOGGER.exception("data hub push failed key=%s", key)

    # ---- 刷新 ---------------------------------------------------------------

    def request(self, key: str, **args: Any) -> dict[str, Any]:
        """刷新命令：排进队列立刻回 ack，刷新出来的数据由 `publish()` 广播给订阅者。"""
        result = self._enqueue(key, args)
        if result["ok"]:
            LOGGER.debug("data hub requested key=%s", key)
        return result

    def _enqueue(self, key: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """把一次刷新排进队列。周期任务走这里 —— 每 0.4 秒问一次的轮询不该往日志里写字。"""
        with self._lock:
            if key not in self._refreshers:
                return {"ok": False, "key": key, "code": "unknown_key"}
            self._pending[key] = (self._epoch, dict(args))
            self._ensure_worker()
            revision = self._revisions.get(key, 0)
        self._wake.set()
        return {"ok": True, "key": key, "revision": revision}

    def refresh_now(self, key: str, **args: Any) -> Any:
        """同步刷新一次并写进数据层（启动期的首次拉取用它）。返回写进去的值。"""
        refresher = self._refresher(key)
        value = refresher(**args)
        self.publish(key, value)
        return value

    def refresh_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.request(key)

    # ---- 数据层自己的刷新任务 -------------------------------------------------

    def add_periodic(
            self,
            keys: Iterable[str],
            interval: float,
            *,
            guard: Callable[[], Any] | None = None,
            name: str = "periodic",
    ) -> None:
        """给数据层挂一个周期性 / 变动驱动的刷新任务。

        `guard` 给出一个"变了没有"的令牌（例如游戏目录的指纹版本）：**只在令牌变化时**才去刷，
        这就是"监听文件夹变动"。不给 `guard` 就是纯定时刷新。刷新的发起照样走 `request()`，
        所以算出来的读数一律经 `publish()` 广播 —— 界面不需要为它写第二套取数逻辑。

        任务线程由数据层自己持有，`close()` 时一起停。
        """
        wanted = tuple(str(key) for key in keys)
        with self._lock:
            if self._closing:
                return
            task = threading.Thread(
                target=self._periodic,
                args=(wanted, max(0.05, float(interval)), guard),
                name=f"sprocket-data-{name}",
                daemon=True,
            )
            self._tasks.append(task)
        task.start()
        LOGGER.info("data hub periodic task added name=%s keys=%s interval=%.2f", name, wanted, interval)

    def _periodic(self, keys: tuple[str, ...], interval: float, guard: Callable[[], Any] | None) -> None:
        token = self._guard_token(guard)
        while not self._stopped.wait(interval):
            if self._closing:
                return
            current = self._guard_token(guard)
            if guard is not None and current == token:
                continue
            token = current
            for key in keys:
                self._enqueue(key, {})

    @staticmethod
    def _guard_token(guard: Callable[[], Any] | None) -> Any:
        if guard is None:
            return None
        try:
            return guard()
        except Exception:  # noqa: BLE001 - 指纹读不出来只当这一轮没变
            LOGGER.exception("data hub guard failed")
            return None

    # ---- 生命周期 -----------------------------------------------------------

    def _refresher(self, key: str) -> Refresher:
        with self._lock:
            refresher = self._refreshers.get(key)
        if refresher is None:
            raise KeyError(f"no refresher registered for {key}")
        return refresher

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._work, name="sprocket-data-hub", daemon=True)
        self._worker.start()

    def _take_pending(self) -> list[tuple[str, int, Mapping[str, Any]]]:
        with self._lock:
            pending = [(key, epoch, args) for key, (epoch, args) in self._pending.items()]
            self._pending.clear()
        return pending

    def _work(self) -> None:
        while True:
            self._wake.wait()
            if self._closing:
                return
            self._wake.clear()
            for key, epoch, args in self._take_pending():
                if self._closing:
                    return
                if self._stale(epoch):
                    LOGGER.info("data hub skipped a stale refresh key=%s epoch=%d", key, epoch)
                    continue
                try:
                    value = self._refresher(key)(**args)
                except Exception as exc:  # noqa: BLE001 - 一个 key 刷不出来不影响别的
                    if getattr(exc, "code", ""):
                        # 带 code 的领域错误是「这一轮算不出来」（例如同一份目录正在别处加载）：
                        # 值留上一次那份，等下一轮；这不是要看的栈。
                        LOGGER.debug("data hub refresh skipped key=%s error=%s", key, exc)
                    else:
                        LOGGER.exception("data hub refresh failed key=%s", key)
                    continue
                # 刷新期间可能刚被作废（换目录）：结果一律丢掉，不让旧目录的读数回来。
                if self._stale(epoch):
                    LOGGER.info("data hub dropped a stale refresh key=%s epoch=%d", key, epoch)
                    continue
                self.publish(key, value)

    def _stale(self, epoch: int) -> bool:
        with self._lock:
            return epoch != self._epoch

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._closing = True
            worker = self._worker
            self._worker = None
            tasks, self._tasks = list(self._tasks), []
        self._stopped.set()
        self._wake.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        for task in tasks:
            if task.is_alive():
                task.join(timeout=timeout)
