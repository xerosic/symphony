from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Deque, Dict, List, Optional
from urllib.parse import urlparse

from discord import VoiceClient
from loguru import logger
import psutil  # type: ignore[import-untyped]

ALLOWED_SITES: List[str] = [
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "on.soundcloud.com",
    "soundcloud.com",
]


@dataclass(slots=True)
class TrackRequestItem:
    id: str
    title: str
    url: str
    length: int  # in seconds
    provider: str
    thumbnail: Optional[str] = None
    stream_bitrate: Optional[int] = None


@dataclass(slots=True)
class StreamInfo:
    stream_url: str
    bitrate: Optional[int] = None


class TrackQueueManager:
    def __init__(self) -> None:
        self.queueDict: Dict[str, Deque[TrackRequestItem]] = {}
        logger.debug("initialized queue manager.")

    def append(self, guild_id: str, trackRequest: TrackRequestItem) -> None:
        if self.queueDict.get(guild_id) is None:
            logger.debug(f"queue for {guild_id} does not exist, creating it...")
            self.queueDict[guild_id] = deque()

        self.queueDict[guild_id].append(trackRequest)
        logger.debug(
            f"added track request {trackRequest.id} for guild {guild_id} to queue."
        )

    def get_next(self, guild_id: str) -> Optional[TrackRequestItem]:
        guild_queue = self.queueDict.get(guild_id)
        if guild_queue is None:
            return None

        if not guild_queue:
            logger.debug(f"queue for guild {guild_id} is empty, dropping it...")
            self.queueDict.pop(guild_id)
            return None

        return guild_queue.popleft()

    def drop_queue(self, guild_id: str) -> bool:
        if guild_id not in self.queueDict:
            return False

        self.queueDict.pop(guild_id)
        logger.debug(f"dropped queue for guild {guild_id}.")
        return True

    def is_empty(self, guild_id: str) -> bool:
        guild_queue = self.queueDict.get(guild_id)
        return not guild_queue

    def get_queue_length(self, guild_id: str) -> int:
        guild_queue = self.queueDict.get(guild_id)
        return len(guild_queue) if guild_queue else 0


class VolumeManager:
    def __init__(self) -> None:
        self.volumeDict: Dict[str, float] = {}
        logger.debug("initialized volume manager.")

    def get_volume(self, guild_id: Optional[str]) -> float:
        if guild_id is None:
            return 1.0
        return self.volumeDict.get(guild_id, 1.0)

    def set_volume(self, guild_id: str, volume: float) -> None:
        self.volumeDict[guild_id] = volume


def is_vc_empty(voice_client: VoiceClient) -> bool:
    if not voice_client or not voice_client.channel:
        return False

    human_members = [
        member for member in voice_client.channel.members if not member.bot
    ]
    return len(human_members) == 0


def is_valid_url(testString: str) -> bool:
    result = urlparse(testString)

    if not testString.startswith("http"):
        return False

    if result.path == "":
        logger.debug(f"Invalid URL: {testString} has no path.")
        return False

    if result.netloc not in ALLOWED_SITES:
        logger.debug(f"Invalid URL: {testString} is not from an allowed site.")
        return False

    return True


def escape_markdown(text: str) -> str:
    markdown_chars = ["*", "_", "`", "~", "\\", "|"]
    for char in markdown_chars:
        text = text.replace(char, f"\\{char}")
    return text


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None or seconds == 0:
        return "Unknown"

    duration = timedelta(seconds=seconds)
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def get_cpu_usage() -> float:
    return psutil.cpu_percent()
