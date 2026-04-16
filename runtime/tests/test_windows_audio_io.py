"""Tests for Windows speech input/output adapter."""

from __future__ import annotations

import subprocess
import unittest

from runtime.voice import WindowsAudioIO, WindowsAudioIoError


class WindowsAudioIOTests(unittest.TestCase):
    def test_speak_invokes_powershell_with_text_payload(self) -> None:
        observed: dict[str, object] = {}

        def fake_runner(command, *, capture_output, text, timeout, env):
            observed["command"] = command
            observed["capture_output"] = capture_output
            observed["text"] = text
            observed["timeout"] = timeout
            observed["env"] = env
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        adapter = WindowsAudioIO(system_name="Windows", runner=fake_runner, speech_rate=3)

        spoken = adapter.speak("Hello Boss")

        self.assertTrue(spoken)
        self.assertEqual(observed["capture_output"], True)
        self.assertEqual(observed["text"], True)
        self.assertEqual(observed["timeout"], 20)
        env = observed["env"]
        self.assertEqual(env["FRIDAY_TTS_TEXT"], "Hello Boss")
        self.assertEqual(env["FRIDAY_TTS_RATE"], "3")

    def test_listen_once_returns_transcript_when_present(self) -> None:
        def fake_runner(command, *, capture_output, text, timeout, env):
            return subprocess.CompletedProcess(command, 0, stdout="status report ready\n", stderr="")

        adapter = WindowsAudioIO(system_name="Windows", runner=fake_runner)

        transcript = adapter.listen_once(timeout_seconds=6)

        self.assertEqual(transcript, "status report ready")

    def test_listen_once_returns_none_when_no_transcript(self) -> None:
        def fake_runner(command, *, capture_output, text, timeout, env):
            return subprocess.CompletedProcess(command, 0, stdout="   \n", stderr="")

        adapter = WindowsAudioIO(system_name="Windows", runner=fake_runner)

        transcript = adapter.listen_once()

        self.assertIsNone(transcript)

    def test_audio_operations_fail_on_non_windows(self) -> None:
        adapter = WindowsAudioIO(system_name="Linux")

        with self.assertRaises(WindowsAudioIoError):
            adapter.speak("hello")

        with self.assertRaises(WindowsAudioIoError):
            adapter.listen_once()

    def test_missing_powershell_binary_surfaces_clear_error(self) -> None:
        def fake_runner(command, *, capture_output, text, timeout, env):
            raise FileNotFoundError("missing")

        adapter = WindowsAudioIO(
            system_name="Windows",
            runner=fake_runner,
            powershell_executable="C:/not-real/powershell.exe",
        )

        with self.assertRaises(WindowsAudioIoError):
            adapter.speak("hello")

    def test_powershell_timeout_surfaces_clear_error(self) -> None:
        def fake_runner(command, *, capture_output, text, timeout, env):
            raise subprocess.TimeoutExpired(command, timeout)

        adapter = WindowsAudioIO(
            system_name="Windows",
            runner=fake_runner,
            powershell_executable="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        )

        with self.assertRaises(WindowsAudioIoError):
            adapter.speak("hello")


if __name__ == "__main__":
    unittest.main()
