"""日志读取：行号必须对得上原文件，取舍只丢行不改号。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure import log_reader
from sprocket_mod_manager.infrastructure.log_reader import (
    READ_MISSING,
    READ_OK,
    READ_TOO_LARGE,
    read_log_lines,
)


class LogReaderTests(unittest.TestCase):
    def _read(self, text: str, name: str = "Latest.log"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(text, encoding="utf-8")
            return read_log_lines(path, role="loader_log", source=name)

    def test_every_line_survives_with_its_own_number(self) -> None:
        result = self._read("one\ntwo\nthree\n")

        self.assertEqual(result.status, READ_OK)
        self.assertFalse(result.truncated)
        self.assertEqual([line["number"] for line in result.lines], [1, 2, 3])
        self.assertEqual([line["text"] for line in result.lines], ["one", "two", "three"])
        self.assertEqual(result.lines[0]["role"], "loader_log")
        self.assertEqual(result.lines[0]["source"], "Latest.log")

    def test_a_file_that_is_not_there_is_said_not_guessed(self) -> None:
        result = read_log_lines(Path("no/such/Latest.log"), role="loader_log", source="x")

        self.assertEqual(result.status, READ_MISSING)
        self.assertEqual(result.lines, ())
        self.assertFalse(result.readable)

    def test_no_path_at_all_is_the_same_as_missing(self) -> None:
        self.assertEqual(read_log_lines(None, role="loader_log", source="x").status, READ_MISSING)

    def test_a_log_too_large_to_read_is_refused_instead_of_misnumbered(self) -> None:
        """交一份行号对不上的证据，比说「这份太大没读」更坏。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_bytes(b"x" * (log_reader.MAX_BYTES + 1))
            result = read_log_lines(path, role="unity_log", source="Player.log")

        self.assertEqual(result.status, READ_TOO_LARGE)
        self.assertEqual(result.lines, ())
        self.assertTrue(result.truncated)

    def test_an_over_long_log_keeps_both_ends_at_their_true_line_numbers(self) -> None:
        total = log_reader.MAX_LINES + 500
        body = "\n".join(
            "engine boot" if number == 1 else f"line {number}" for number in range(1, total + 1)
        )

        result = self._read(body + "\n")

        self.assertTrue(result.truncated)
        numbers = [line["number"] for line in result.lines]
        self.assertEqual(len(numbers), log_reader.MAX_LINES)
        self.assertEqual(numbers[0], 1, "开头那段是启动阶段，必须留着")
        self.assertEqual(numbers[-1], total, "结尾那段是出事之前，必须留着")
        self.assertEqual(result.lines[0]["text"], "engine boot")
        self.assertEqual(result.lines[-1]["text"], f"line {total}")
        self.assertEqual(numbers, sorted(numbers), "行号仍然是有序的、不重复的")

    def test_a_log_exactly_at_the_limit_is_not_truncated(self) -> None:
        body = "\n".join(f"line {number}" for number in range(1, log_reader.MAX_LINES + 1))

        result = self._read(body + "\n")

        self.assertFalse(result.truncated)
        self.assertEqual(len(result.lines), log_reader.MAX_LINES)

    def test_undecodable_bytes_do_not_lose_the_rest_of_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Latest.log"
            path.write_bytes(b"fine\n\xff\xfe broken\nstill fine\n")
            result = read_log_lines(path, role="loader_log", source="Latest.log")

        self.assertEqual(result.status, READ_OK)
        self.assertEqual(len(result.lines), 3)
        self.assertEqual(result.lines[0]["text"], "fine")
        self.assertEqual(result.lines[2]["text"], "still fine")


if __name__ == "__main__":
    unittest.main()
