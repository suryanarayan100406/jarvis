"""Voice module exports."""

from .speech_adapters import SpeechFrame, StreamingSttAdapter, StreamingTtsAdapter, TtsChunk
from .turn_manager import ConversationTurn, ConversationTurnManager, TurnInterruption, TurnStateError
from .wake_trigger import WakeDetection, WakePhraseDetector
from .windows_audio_io import WindowsAudioIO, WindowsAudioIoError

__all__ = [
	"WakePhraseDetector",
	"WakeDetection",
	"StreamingSttAdapter",
	"SpeechFrame",
	"StreamingTtsAdapter",
	"TtsChunk",
	"ConversationTurnManager",
	"ConversationTurn",
	"TurnInterruption",
	"TurnStateError",
	"WindowsAudioIO",
	"WindowsAudioIoError",
]
