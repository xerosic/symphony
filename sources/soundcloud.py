from __future__ import annotations

import asyncio
from collections import OrderedDict
from time import time
from typing import Dict, Optional, Tuple

import discord
import yt_dlp  # type: ignore[import-untyped]
from loguru import logger

from utils import StreamInfo, TrackRequestItem


class SoundCloudSource:
    def __init__(self) -> None:
        self.ytdl_format_options = {
            "format": "bestaudio[ext!=m3u8]/best[ext!=m3u8]",
            "noplaylist": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "logtostderr": False,
            "quiet": True,
            "no_warnings": True,
            "default_search": "auto",
            "source_address": "0.0.0.0",
            "geo_bypass": True,
            "extractaudio": True,
            "audioformat": "mp3",
        }

        self.ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_http_error 4xx,5xx -nostdin -loglevel warning -probesize 64k -analyzeduration 0",
            "options": "-vn -sn -dn -bufsize 512k",
        }

        self.ytdl = yt_dlp.YoutubeDL(self.ytdl_format_options)
        self._stream_cache: "OrderedDict[str, Tuple[StreamInfo, float]]" = OrderedDict()
        self._cache_ttl = 900
        self._cache_max_entries = 128
        self._inflight: Dict[str, asyncio.Task[StreamInfo]] = {}

    async def search(self, query: str) -> TrackRequestItem:
        loop = asyncio.get_running_loop()

        try:
            if not query.startswith("http"):
                query = f"scsearch:{query}"

            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(query, download=False)
            )

            if "entries" in data:
                entries = data.get("entries") or []
                if not entries:
                    raise ValueError("No tracks found on SoundCloud for this query")
                data = entries[0]

            return TrackRequestItem(
                id=data.get("id", ""),
                title=data.get("title", "Unknown"),
                url=data.get("webpage_url", ""),
                length=data.get("duration", 0),
                provider="SoundCloud",
                thumbnail=data.get("thumbnail"),
                stream_bitrate=data.get("abr"),
            )

        except Exception as exc:
            if isinstance(exc, yt_dlp.utils.DownloadError):
                error_text = str(exc)
                if "404" in error_text or "not found" in error_text.lower():
                    logger.error(f"soundCloud track not found: {query}")
                    raise ValueError(
                        "❌ SoundCloud track not found. The link may be invalid or the track may have been removed."
                    ) from exc
                logger.error(f"download error while searching SoundCloud: {exc}")
                raise ValueError(
                    f"❌ Error accessing SoundCloud: {error_text}"
                ) from exc
            logger.error(f"error searching SoundCloud: {exc}")
            raise

    async def resolve_stream(self, track: TrackRequestItem) -> StreamInfo:
        cache_key = track.url
        cached = self._get_cached_stream(cache_key)
        if cached:
            return cached

        inflight = self._inflight.get(cache_key)
        if inflight:
            return await inflight

        task = asyncio.create_task(self._download_stream(cache_key))
        self._inflight[cache_key] = task

        try:
            stream = await task
        finally:
            self._inflight.pop(cache_key, None)

        self._remember_stream(cache_key, stream)
        return stream

    async def get_audio_source(
        self,
        track: TrackRequestItem,
        volume: float,
        stream_info: Optional[StreamInfo] = None,
    ) -> discord.PCMVolumeTransformer:
        stream = stream_info or await self.resolve_stream(track)
        audio_source = discord.FFmpegPCMAudio(
            stream.stream_url,
            **self.ffmpeg_options,  # type: ignore[arg-type]
        )
        return discord.PCMVolumeTransformer(audio_source, volume=volume)

    async def _download_stream(self, url: str) -> StreamInfo:
        loop = asyncio.get_running_loop()

        try:
            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(url, download=False)
            )
            stream_url = self._extract_stream_url(data)
            bitrate = data.get("abr") or self._get_best_bitrate(data)
            return StreamInfo(stream_url=stream_url, bitrate=bitrate)
        except Exception as exc:
            logger.error(f"error getting SoundCloud audio source for {url}: {exc}")
            if isinstance(exc, yt_dlp.utils.DownloadError):
                error_text = str(exc)
                if "404" in error_text or "not found" in error_text.lower():
                    raise ValueError(
                        "❌ SoundCloud track is no longer available"
                    ) from exc
                raise ValueError(
                    f"❌ Error accessing SoundCloud: {error_text}"
                ) from exc
            raise ValueError(f"❌ Failed to get audio source: {exc}") from exc

    def _extract_stream_url(self, data: dict) -> str:
        formats = data.get("formats") or []
        preferred_formats = [
            fmt
            for fmt in formats
            if fmt.get("url")
            and not str(fmt.get("url")).endswith(".m3u8")
            and fmt.get("acodec")
            and fmt.get("acodec") != "none"
        ]

        if preferred_formats:
            preferred_formats.sort(key=lambda fmt: fmt.get("abr", 0), reverse=True)
            return preferred_formats[0]["url"]

        fallback_formats = [
            fmt
            for fmt in formats
            if fmt.get("url") and fmt.get("acodec") and fmt.get("acodec") != "none"
        ]

        if fallback_formats:
            return fallback_formats[0]["url"]

        if data.get("url") and data["url"].startswith("http"):
            return data["url"]

        raise ValueError("No valid audio stream found")

    def _get_best_bitrate(self, data: dict) -> Optional[int]:
        formats = data.get("formats") or []
        candidates = [fmt.get("abr") for fmt in formats if fmt.get("abr")]
        if not candidates:
            return None
        return int(max(candidates))

    def _get_cached_stream(self, cache_key: str) -> Optional[StreamInfo]:
        cached = self._stream_cache.get(cache_key)
        if not cached:
            return None

        stream, expires_at = cached
        if expires_at < time():
            self._stream_cache.pop(cache_key, None)
            return None
        return stream

    def _remember_stream(self, cache_key: str, stream: StreamInfo) -> None:
        self._stream_cache[cache_key] = (stream, time() + self._cache_ttl)
        self._stream_cache.move_to_end(cache_key)
        if len(self._stream_cache) > self._cache_max_entries:
            self._stream_cache.popitem(last=False)
