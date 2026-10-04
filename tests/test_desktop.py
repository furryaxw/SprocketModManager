from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.desktop import open_directory, reveal_in_file_manager

MODULE = "sprocket_mod_manager.infrastructure.desktop"


def _completed(returncode: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, b"", b"")


class WindowsLocationTests(unittest.TestCase):
    def test_reveal_selects_the_file_in_explorer(self) -> None:
        with TemporaryDirectory() as directory:
            target = (Path(directory) / "Mod.dll").resolve()
            target.write_text("x", encoding="utf-8")

            with patch(f"{MODULE}.os.name", "nt"), patch(f"{MODULE}.subprocess.Popen") as popen:
                reveal_in_file_manager(target)

            popen.assert_called_once_with(["explorer", f"/select,{target}"])

    def test_a_location_that_is_not_on_disk_is_reported(self) -> None:
        with TemporaryDirectory() as directory:
            target = (Path(directory) / "gone" / "Mod.dll").resolve()

            with patch(f"{MODULE}.os.name", "nt"), self.assertRaises(OSError):
                reveal_in_file_manager(target)


class LinuxLocationTests(unittest.TestCase):
    def test_open_directory_uses_xdg_open(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory).resolve()

            with patch(f"{MODULE}.os.name", "posix"), patch(f"{MODULE}.subprocess.Popen") as popen:
                open_directory(target)

            popen.assert_called_once_with(["xdg-open", str(target)])

    def test_reveal_selects_through_the_freedesktop_interface(self) -> None:
        with TemporaryDirectory() as directory:
            target = (Path(directory) / "Mod.dll").resolve()
            target.write_text("x", encoding="utf-8")

            with (
                patch(f"{MODULE}.os.name", "posix"),
                patch(f"{MODULE}.subprocess.run", return_value=_completed(0)) as run,
                patch(f"{MODULE}.subprocess.Popen") as popen,
            ):
                reveal_in_file_manager(target)

            command = run.call_args.args[0]
            self.assertEqual(command[:5], [
                "dbus-send", "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
                "/org/freedesktop/FileManager1",
            ])
            self.assertIn(f"array:string:{target.as_uri()}", command)
            popen.assert_not_called()

    def test_reveal_opens_the_directory_when_the_desktop_cannot_select(self) -> None:
        with TemporaryDirectory() as directory:
            target = (Path(directory) / "Mod.dll").resolve()
            target.write_text("x", encoding="utf-8")

            with (
                patch(f"{MODULE}.os.name", "posix"),
                patch(f"{MODULE}.subprocess.run", return_value=_completed(1)),
                patch(f"{MODULE}.subprocess.Popen") as popen,
            ):
                reveal_in_file_manager(target)

            popen.assert_called_once_with(["xdg-open", str(target.parent)])

    def test_a_missing_file_reveals_its_directory(self) -> None:
        with TemporaryDirectory() as directory:
            target = (Path(directory) / "Mod.dll").resolve()

            with patch(f"{MODULE}.os.name", "posix"), patch(f"{MODULE}.subprocess.Popen") as popen:
                reveal_in_file_manager(target)

            popen.assert_called_once_with(["xdg-open", str(target.parent)])

    def test_a_missing_xdg_open_is_reported(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory).resolve()

            with (
                patch(f"{MODULE}.os.name", "posix"),
                patch(f"{MODULE}.subprocess.Popen", side_effect=FileNotFoundError),
                self.assertRaises(OSError),
            ):
                open_directory(target)


if __name__ == "__main__":
    unittest.main()
