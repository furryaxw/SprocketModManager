import logging
import threading
import time
import unittest

from sprocket_mod_manager.application.data_hub import (
    KEY_INSTALLED,
    KEY_LOADERS,
    DataHub,
)
from sprocket_mod_manager.domain.errors import CatalogBusyError


class DataHubTests(unittest.TestCase):
    """数据层的契约：一份数据、按 key 通知、get 不碰 I/O、request 只回 ack。"""

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.hub = DataHub(pusher=self.events.append)

    def tearDown(self) -> None:
        self.hub.close()

    def test_get_never_touches_the_refresher(self) -> None:
        calls: list[int] = []
        self.hub.register(KEY_INSTALLED, lambda: calls.append(1) or ["a"])

        self.assertIsNone(self.hub.get(KEY_INSTALLED), "还没刷过就是没有值")
        self.assertFalse(self.hub.has(KEY_INSTALLED))
        self.assertEqual(calls, [], "get 是读内存，不许触发 I/O")

    def test_a_write_notifies_only_the_subscribers_of_that_key(self) -> None:
        self.hub.subscribe("ui", [KEY_INSTALLED])

        self.hub.publish(KEY_INSTALLED, [{"id": "a"}])
        self.hub.publish(KEY_LOADERS, [{"id": "b"}])

        self.assertEqual([event["key"] for event in self.events], [KEY_INSTALLED])
        self.assertEqual(self.events[0]["value"], [{"id": "a"}])
        self.assertEqual(self.events[0]["revision"], 1)

    def test_nothing_is_pushed_while_nobody_listens(self) -> None:
        self.hub.publish(KEY_INSTALLED, [{"id": "a"}])

        self.assertEqual(self.events, [], "没订阅就不推，省得白惊动界面")
        self.assertEqual(self.hub.get(KEY_INSTALLED), [{"id": "a"}], "但值确实存下来了")

    def test_unsubscribing_stops_the_push(self) -> None:
        self.hub.subscribe("ui", [KEY_INSTALLED])
        self.hub.publish(KEY_INSTALLED, [{"id": "a"}])
        self.events.clear()

        self.hub.unsubscribe("ui", [KEY_INSTALLED])
        self.hub.publish(KEY_INSTALLED, [{"id": "b"}])

        self.assertEqual(self.events, [])
        self.assertEqual(self.hub.subscribers(KEY_INSTALLED), frozenset())

    def test_rewriting_the_same_value_is_not_a_change(self) -> None:
        self.hub.subscribe("ui", [KEY_INSTALLED])
        self.hub.publish(KEY_INSTALLED, [{"id": "a"}])
        self.events.clear()

        changed = self.hub.publish(KEY_INSTALLED, [{"id": "a"}])

        self.assertFalse(changed, "值没变就不算一次变更")
        self.assertEqual(self.events, [], "界面不该白重画")
        self.assertEqual(self.hub.revision(KEY_INSTALLED), 1)

    def test_subscribe_hands_back_the_current_snapshot(self) -> None:
        self.hub.publish(KEY_INSTALLED, [{"id": "a"}])

        snapshot = self.hub.subscribe("ui", [KEY_INSTALLED, KEY_LOADERS])

        self.assertEqual(snapshot[KEY_INSTALLED]["value"], [{"id": "a"}])
        self.assertEqual(snapshot[KEY_INSTALLED]["revision"], 1)
        self.assertTrue(snapshot[KEY_INSTALLED]["known"])
        self.assertFalse(snapshot[KEY_LOADERS]["known"], "还没刷过的 key 如实报告没有值")

    def test_request_only_answers_with_an_ack_and_pushes_the_value_back(self) -> None:
        arrived = threading.Event()
        self.hub.register(KEY_INSTALLED, lambda source="": [{"id": source or "auto"}])
        self.hub.subscribe("ui", [KEY_INSTALLED])
        self.events.clear()
        self.hub.set_pusher(lambda event: (self.events.append(event), arrived.set()))

        ack = self.hub.request(KEY_INSTALLED, source="Mods")

        self.assertTrue(ack["ok"], ack)
        self.assertNotIn("value", ack, "刷新命令不许把数据当返回值带回来")
        self.assertTrue(arrived.wait(5), "刷新完必须推给订阅者")
        self.assertEqual(self.events[-1]["value"], [{"id": "Mods"}])

    def test_request_for_a_key_without_a_refresher_is_reported(self) -> None:
        ack = self.hub.request("nope")

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["code"], "unknown_key")

    def test_a_broken_pusher_does_not_break_the_data_layer(self) -> None:
        def explode(_event: dict) -> None:
            raise RuntimeError("window is gone")

        self.hub.subscribe("ui", [KEY_INSTALLED])
        self.hub.set_pusher(explode)

        self.assertTrue(self.hub.publish(KEY_INSTALLED, [{"id": "a"}]))
        self.assertEqual(self.hub.get(KEY_INSTALLED), [{"id": "a"}])

    def test_a_refresh_started_before_an_invalidation_is_dropped(self) -> None:
        """换目录（作废）之后，还在路上的旧目录读数不许再推上界面。"""
        started = threading.Event()
        release = threading.Event()

        def slow() -> list:
            started.set()
            release.wait(5)
            return [{"id": "old-dir"}]

        self.hub.register(KEY_INSTALLED, slow)
        self.hub.subscribe("ui", [KEY_INSTALLED])
        self.events.clear()

        self.hub.request(KEY_INSTALLED)
        self.assertTrue(started.wait(5), "刷新要真的跑起来")
        self.hub.invalidate([KEY_INSTALLED])
        release.set()
        deadline = time.time() + 5
        while time.time() < deadline and len(self.events) < 2:
            time.sleep(0.02)
        time.sleep(0.1)

        self.assertEqual(
            [event["value"] for event in self.events],
            [None],
            "只留「作废」那一条，作废之前发起的刷新结果被丢掉",
        )
        self.assertIsNone(self.hub.get(KEY_INSTALLED), "旧目录的读数不许落回来")

    def test_a_guarded_periodic_task_refreshes_only_when_its_token_changes(self) -> None:
        """数据层自己的监听任务：令牌没变就不刷，变了才刷（文件变动监听就是这么用的）。"""
        self.hub.register(KEY_INSTALLED, lambda: [{"id": "reading"}])
        self.hub.subscribe("ui", [KEY_INSTALLED])
        token = {"value": 1}
        self.hub.add_periodic([KEY_INSTALLED], 0.05, guard=lambda: token["value"], name="test")

        time.sleep(0.2)
        self.assertEqual(self.events, [], "令牌没变就不许刷")

        token["value"] = 2
        deadline = time.time() + 5
        while time.time() < deadline and not self.events:
            time.sleep(0.02)

        self.assertTrue(self.events, "令牌一变就要自己刷一次")
        self.assertEqual(self.events[-1]["value"], [{"id": "reading"}])

    def test_a_periodic_poll_stays_out_of_the_log(self) -> None:
        """周期轮询不写日志：只有界面明确发来的刷新命令才留一行。"""
        records: list[logging.LogRecord] = []

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        hub_logger = logging.getLogger("sprocket_mod_manager.application.data_hub")
        collector = _Collect()
        collector.setLevel(logging.DEBUG)
        hub_logger.addHandler(collector)
        hub_logger.setLevel(logging.DEBUG)
        self.addCleanup(hub_logger.removeHandler, collector)
        self.addCleanup(hub_logger.setLevel, logging.NOTSET)

        self.hub.register(KEY_INSTALLED, lambda: [])
        self.hub.add_periodic([KEY_INSTALLED], 0.05, name="test")

        time.sleep(0.3)
        polled = [record.getMessage() for record in records]
        self.assertEqual([text for text in polled if "requested" in text], [], f"轮询不该写日志：{polled}")

        self.hub.request(KEY_INSTALLED)
        asked = [record.getMessage() for record in records]
        self.assertTrue([text for text in asked if "requested" in text], "界面明确要的刷新要留一行")

    def test_close_stops_the_periodic_tasks(self) -> None:
        calls: list[int] = []
        self.hub.register(KEY_INSTALLED, lambda: calls.append(1) or [])
        self.hub.add_periodic([KEY_INSTALLED], 0.05, name="test")

        self.hub.close()
        settled = len(calls)
        time.sleep(0.2)

        self.assertEqual(len(calls), settled, "关掉之后不许再刷")

    def test_a_failing_refresh_leaves_the_previous_value_in_place(self) -> None:
        started = threading.Event()
        release = threading.Event()
        logged = threading.Event()

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.levelno >= logging.ERROR:
                    logged.set()

        def explode() -> list:
            started.set()
            release.wait(5)
            raise RuntimeError("disk is busy")

        handler = _Collect()
        logger = logging.getLogger("sprocket_mod_manager.application.data_hub")
        logger.addHandler(handler)
        self.hub.publish(KEY_INSTALLED, [{"id": "old"}])
        self.hub.register(KEY_INSTALLED, explode)
        try:
            self.hub.request(KEY_INSTALLED)
            self.assertTrue(started.wait(5), "刷新应该真的跑起来")
            release.set()
            self.assertTrue(logged.wait(5), "刷新失败必须留下日志")
        finally:
            logger.removeHandler(handler)

        self.assertEqual(self.hub.get(KEY_INSTALLED), [{"id": "old"}], "刷不出来不能把旧值抹掉")
        self.assertEqual(self.hub.revision(KEY_INSTALLED), 1)

    def test_a_coded_refresh_failure_keeps_the_value_without_a_traceback(self) -> None:
        """带 code 的领域错误是「这一轮算不出来」（同一份读数正在别处算），不是要看的栈。"""
        records: list[logging.LogRecord] = []

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        def busy() -> list:
            raise CatalogBusyError("catalog load is already running")

        handler = _Collect()
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("sprocket_mod_manager.application.data_hub")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.hub.publish(KEY_INSTALLED, [{"id": "old"}])
        self.hub.register(KEY_INSTALLED, busy)
        try:
            self.hub.request(KEY_INSTALLED)
            deadline = time.time() + 5
            while time.time() < deadline and not any("skipped" in item.getMessage() for item in records):
                time.sleep(0.02)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(logging.NOTSET)

        self.assertEqual(self.hub.get(KEY_INSTALLED), [{"id": "old"}], "算不出来的一轮保留上一次读数")
        self.assertTrue([item for item in records if "skipped" in item.getMessage()], "写一条 debug 说明跳过了")
        self.assertEqual([item for item in records if item.levelno >= logging.ERROR], [], "这一轮不是故障")
        self.assertEqual(self.hub.revision(KEY_INSTALLED), 1)


if __name__ == "__main__":
    unittest.main()
