"""日志读取：行号必须对得上原文件，状态说的是这份日志**实际发生了什么**。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.log_reader import (
    READ_MISSING,
    READ_OK,
    READ_UNREADABLE,
    LogStream,
)


class LogStreamTests(unittest.TestCase):
    def _read(self, text: str, name: str = "Latest.log") -> tuple[LogStream, list[dict]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(text, encoding="utf-8")
            with LogStream(path, role="loader_log", source=name) as stream:
                return stream, list(stream)

    def test_every_line_survives_with_its_own_number(self) -> None:
        stream, lines = self._read("one\ntwo\nthree\n")

        self.assertEqual(stream.status, READ_OK)
        self.assertEqual([line["number"] for line in lines], [1, 2, 3])
        self.assertEqual([line["text"] for line in lines], ["one", "two", "three"])
        self.assertEqual(lines[0]["role"], "loader_log")
        self.assertEqual(lines[0]["source"], "Latest.log")
        self.assertEqual(stream.count, 3)

    def test_a_file_that_is_not_there_is_said_not_guessed(self) -> None:
        with LogStream(Path("no/such/Latest.log"), role="loader_log", source="x") as stream:
            lines = list(stream)

        self.assertEqual(stream.status, READ_MISSING)
        self.assertEqual(lines, [])
        self.assertEqual(stream.count, 0)

    def test_no_path_at_all_is_the_same_as_missing(self) -> None:
        with LogStream(None, role="loader_log", source="x") as stream:
            self.assertEqual(list(stream), [])

        self.assertEqual(stream.status, READ_MISSING)

    def test_a_path_that_cannot_be_read_is_told_apart_from_missing(self) -> None:
        """存在但读不了（这里拿目录当例子）不是「这份日志没有」。"""
        with tempfile.TemporaryDirectory() as directory:
            with LogStream(Path(directory), role="unity_log", source="Player.log") as stream:
                lines = list(stream)

        self.assertEqual(stream.status, READ_UNREADABLE)
        self.assertEqual(lines, [])

    def test_a_log_far_past_the_old_size_ceiling_is_read_line_by_line(self) -> None:
        """没有「太大就不读」这回事：一次只拿一行，读到哪算到哪。"""
        body = "".join(f"line {number} {'.' * 90}\n" for number in range(1, 90001))

        stream, lines = self._read(body)

        self.assertGreater(len(body), 8 * 1024 * 1024, "样本本身要超过旧的 8 MiB 上限")
        self.assertEqual(stream.status, READ_OK)
        self.assertEqual(len(lines), 90000)
        self.assertEqual(lines[-1]["number"], 90000)
        self.assertEqual(lines[0]["text"].startswith("line 1 "), True)

    def test_an_empty_file_is_read_not_refused(self) -> None:
        stream, lines = self._read("")

        self.assertEqual(stream.status, READ_OK)
        self.assertEqual(lines, [])

    def test_undecodable_bytes_do_not_lose_the_rest_of_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_bytes(b"fine\n\xff\xfe broken\nstill fine\n")
            with LogStream(path, role="loader_log", source="Latest.log") as stream:
                lines = list(stream)

        self.assertEqual(stream.status, READ_OK)
        self.assertEqual([line["number"] for line in lines], [1, 2, 3])
        self.assertEqual(lines[0]["text"], "fine")
        self.assertEqual(lines[2]["text"], "still fine")

    def test_the_reading_says_what_actually_happened(self) -> None:
        stream, _lines = self._read("one\ntwo\n")

        self.assertEqual(
            stream.reading(),
            {
                "role": "loader_log",
                "label": "Latest.log",
                "path": str(stream.path),
                "status": READ_OK,
                "lines": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
