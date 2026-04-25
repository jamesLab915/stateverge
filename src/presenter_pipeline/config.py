"""
Configuration: StateVerge root, topic paths, ElevenLabs env, tool defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Default StateVerge root: ~/StateVerge
def default_stateverge_root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge"))


@dataclass
class ElevenSettings:
    api_key: str
    voice_id: str
    model_id: str
    stability: float
    similarity_boost: float
    style: float
    use_speaker_boost: bool

    @classmethod
    def from_env(cls) -> "ElevenSettings":
        k = os.environ.get("ELEVENLABS_API_KEY", "")
        if not k:
            raise ValueError("ELEVENLABS_API_KEY is required in .env")
        return cls(
            api_key=k,
            voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
            model_id=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
            stability=float(os.environ.get("ELEVENLABS_STABILITY", "0.5")),
            similarity_boost=float(os.environ.get("ELEVENLABS_SIMILARITY_BOOST", "0.8")),
            style=float(os.environ.get("ELEVENLABS_STYLE", "0.0")),
            use_speaker_boost=os.environ.get("ELEVENLABS_USE_SPEAKER_BOOST", "true").lower()
            in ("1", "true", "yes"),
        )


@dataclass
class PipelineConfig:
    """
    Project-wide settings.

    **Host identity (hard):** the file ``assets/presenter_reference/host_master.png`` is
    the single source of host identity. All presenter clips (masters or future
    *variants*) are *the same person*; prompts must never override face, hair, clothes,
    age, or gender. Allowed prompt axes: topic context, motion scale, camera/lens, mood
    and lighting. See :mod:`.variant_generator` for the stub and policy constants.
    """

    root: Path = field(default_factory=default_stateverge_root)
    # Planning
    insert_interval_sec: float = 60.0
    min_gap_between_anchors_sec: float = 35.0
    min_narrative_tail_sec: float = 20.0
    # Audio
    max_audio_part_sec: float = 30.0
    audio_lipsync_buffer_sec: float = 0.3
    sample_rate: int = 48_000
    audio_channels: int = 2
    # Base video
    target_width: int = 1920
    target_height: int = 1080
    target_fps: int = 30
    # Silence detection (ffmpeg silencedetect)
    silence_db: int = -40
    min_silence_sec: float = 0.25
    # Masters
    master_dirs: tuple = ("5s", "10s")

    def topic_dir(self, topic_slug: str) -> Path:
        return self.root / "topics" / topic_slug

    def assets_masters(self) -> Path:
        return self.root / "assets" / "presenter_masters"

    def assets_presenter_reference(self) -> Path:
        return self.root / "assets" / "presenter_reference"

    def assets_presenter_generated(self) -> Path:
        return self.root / "assets" / "presenter_generated"

    def host_reference_image(self) -> Path:
        return self.root / "assets" / "presenter_reference" / "host_master.png"

    @classmethod
    def load(cls, root: Optional[Path] = None) -> "PipelineConfig":
        c = cls()
        if root is not None:
            c.root = root.expanduser().resolve()
        return c


def load_dotenv_silent(root: Path) -> None:
    """Load .env from StateVerge root if python-dotenv is available."""
    p = root / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(p, override=False)
    except Exception:
        pass  # .env is optional; keys may be in environment


def get_config() -> tuple[PipelineConfig, Optional[ElevenSettings]]:
    """Load .env, return PipelineConfig and ElevenSettings if key present."""
    root = default_stateverge_root()
    load_dotenv_silent(root)
    pc = PipelineConfig.load(root)
    el: Optional[ElevenSettings] = None
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            el = ElevenSettings.from_env()
        except Exception:
            el = None
    return pc, el
