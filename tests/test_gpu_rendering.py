from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.gpu_rendering import (
    ATTEMPT_MARKER,
    DISABLED_MARKER,
    mark_attempt,
    record_failure,
    software_rendering,
)


class GpuRenderingPolicyTests(unittest.TestCase):
    def test_a_fresh_directory_uses_hardware_acceleration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)

            self.assertFalse(software_rendering(app_dir))
            self.assertFalse((app_dir / ATTEMPT_MARKER).exists())
            self.assertFalse((app_dir / DISABLED_MARKER).exists())

    def test_an_attempt_that_never_finished_switches_to_software(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            mark_attempt(app_dir)

            self.assertTrue(software_rendering(app_dir), "上一次没活到 UI 起来")
            self.assertFalse((app_dir / ATTEMPT_MARKER).exists(), "进行中的标记用完就删")
            self.assertTrue((app_dir / DISABLED_MARKER).is_file())

            self.assertTrue(software_rendering(app_dir), "记下来了：之后默认软件渲染")

    def test_the_flag_forces_software_rendering_without_recording_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)

            self.assertTrue(software_rendering(app_dir, force_off=True))
            self.assertFalse((app_dir / DISABLED_MARKER).exists(), "一次性开关不写状态")

    def test_trying_hardware_again_clears_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            record_failure(app_dir)

            self.assertFalse(software_rendering(app_dir, force_on=True))
            self.assertFalse((app_dir / DISABLED_MARKER).exists())
            self.assertFalse(software_rendering(app_dir), "记录没了就回到默认的硬件加速")

    def test_a_finished_attempt_keeps_hardware_acceleration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            mark_attempt(app_dir).unlink()  # UI 起来了：进行中的标记被删掉

            self.assertFalse(software_rendering(app_dir))
            self.assertFalse((app_dir / DISABLED_MARKER).exists())

    def test_record_failure_clears_the_attempt_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            mark_attempt(app_dir)

            record_failure(app_dir)

            self.assertFalse((app_dir / ATTEMPT_MARKER).exists())
            self.assertTrue((app_dir / DISABLED_MARKER).is_file())

    def test_a_missing_manager_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory) / "nested" / "manager"

            mark_attempt(app_dir)

            self.assertTrue((app_dir / ATTEMPT_MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
