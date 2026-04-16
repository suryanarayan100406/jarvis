"""Windows speech input/output adapter using System.Speech via PowerShell."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

PowerShellRunner = Callable[..., subprocess.CompletedProcess[str]]


_SPEAK_SCRIPT = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$rate = [int]$env:FRIDAY_TTS_RATE
if ($rate -lt -10) { $rate = -10 }
if ($rate -gt 10) { $rate = 10 }
$synth.Rate = $rate

$preferredVoice = $env:FRIDAY_TTS_VOICE
$preferredCulture = $env:FRIDAY_TTS_LANGUAGE
$voiceSelected = $false

if (-not [string]::IsNullOrWhiteSpace($preferredVoice)) {
    try {
        $synth.SelectVoice($preferredVoice)
        $voiceSelected = $true
    } catch {
    }
}

if (-not $voiceSelected -and -not [string]::IsNullOrWhiteSpace($preferredCulture)) {
    foreach ($installed in $synth.GetInstalledVoices()) {
        $info = $installed.VoiceInfo
        if ($null -ne $info -and $null -ne $info.Culture) {
            if ($info.Culture.Name -like "$preferredCulture*") {
                try {
                    $synth.SelectVoice($info.Name)
                    $voiceSelected = $true
                    break
                } catch {
                }
            }
        }
    }
}

if (-not $voiceSelected -and $preferredCulture -eq "hi-IN") {
    foreach ($candidate in @("Microsoft Heera Desktop", "Microsoft Kalpana Desktop", "Microsoft Ravi Desktop")) {
        try {
            $synth.SelectVoice($candidate)
            $voiceSelected = $true
            break
        } catch {
        }
    }
}

$text = $env:FRIDAY_TTS_TEXT
if (-not [string]::IsNullOrWhiteSpace($text)) {
    $synth.Speak($text)
}
""".strip()


_LISTEN_SCRIPT = """
Add-Type -AssemblyName System.Speech
$timeout = [int]$env:FRIDAY_STT_TIMEOUT
if ($timeout -lt 1) { $timeout = 1 }

try {
    $culture = [System.Globalization.CultureInfo]::InstalledUICulture
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
} catch {
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
}

$recognizer.SetInputToDefaultAudioDevice()
$recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$result = $recognizer.Recognize([TimeSpan]::FromSeconds($timeout))
if ($null -ne $result) {
    Write-Output $result.Text
}
""".strip()


class WindowsAudioIoError(RuntimeError):
    """Raised when Windows speech input/output cannot be completed."""


class WindowsAudioIO:
    """Speech input/output wrapper for local Windows assistant sessions."""

    def __init__(
        self,
        *,
        powershell_executable: str = "powershell.exe",
        speech_rate: int = 0,
        voice_language: str = "en-US",
        voice_name: str | None = None,
        stt_timeout_seconds: int = 8,
        runner: PowerShellRunner | None = None,
        system_name: str | None = None,
    ) -> None:
        self.powershell_executable = powershell_executable
        self.speech_rate = int(speech_rate)
        self.voice_language = str(voice_language).strip() or "en-US"
        self.voice_name = _normalize_text(voice_name or "")
        self.stt_timeout_seconds = max(1, int(stt_timeout_seconds))
        self._runner = runner or subprocess.run
        self._system_name = (system_name or platform.system()).strip().lower()

    def is_supported(self) -> bool:
        return self._system_name == "windows"

    def speak(self, text: str) -> bool:
        normalized = _normalize_text(text)
        if not normalized:
            return False

        self._require_windows("text-to-speech")
        env = os.environ.copy()
        env["FRIDAY_TTS_TEXT"] = normalized
        env["FRIDAY_TTS_RATE"] = str(self.speech_rate)
        env["FRIDAY_TTS_LANGUAGE"] = self.voice_language
        env["FRIDAY_TTS_VOICE"] = self.voice_name

        completed = self._run_powershell(_SPEAK_SCRIPT, env=env, timeout_seconds=20)
        self._ensure_success(completed, action="text-to-speech")
        return True

    def listen_once(self, *, timeout_seconds: int | None = None) -> str | None:
        self._require_windows("speech-to-text")

        resolved_timeout = self.stt_timeout_seconds if timeout_seconds is None else max(1, int(timeout_seconds))
        env = os.environ.copy()
        env["FRIDAY_STT_TIMEOUT"] = str(resolved_timeout)

        completed = self._run_powershell(
            _LISTEN_SCRIPT,
            env=env,
            timeout_seconds=resolved_timeout + 5,
        )
        self._ensure_success(completed, action="speech-to-text")

        transcript = _normalize_text(completed.stdout)
        return transcript or None

    def _run_powershell(
        self,
        script: str,
        *,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        executable = _resolve_powershell_executable(self.powershell_executable)
        try:
            return self._runner(
                [executable, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )
        except FileNotFoundError as exc:
            raise WindowsAudioIoError(
                f"PowerShell executable not found: {self.powershell_executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WindowsAudioIoError(
                f"PowerShell command timed out after {timeout_seconds} seconds"
            ) from exc

    def _require_windows(self, capability: str) -> None:
        if not self.is_supported():
            raise WindowsAudioIoError(f"{capability} is only supported on Windows")

    @staticmethod
    def _ensure_success(result: subprocess.CompletedProcess[str], *, action: str) -> None:
        if result.returncode == 0:
            return

        detail = _normalize_text(result.stderr) or _normalize_text(result.stdout) or "unknown error"
        raise WindowsAudioIoError(f"{action} failed: {detail}")


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split())


def _resolve_powershell_executable(preferred: str) -> str:
    preferred_normalized = str(preferred).strip()
    if preferred_normalized:
        preferred_path = Path(preferred_normalized)
        if preferred_path.is_file():
            return str(preferred_path)

        located = shutil.which(preferred_normalized)
        if located:
            return located

    # Fallback for environments where PATH omits PowerShell but system install exists.
    canonical = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if canonical.is_file():
        return str(canonical)

    raise WindowsAudioIoError(f"PowerShell executable not found: {preferred_normalized or 'powershell.exe'}")


__all__ = ["WindowsAudioIO", "WindowsAudioIoError"]
