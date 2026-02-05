from __future__ import annotations

import asyncio
from collections import OrderedDict
import os
from time import time
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import yt_dlp  # type: ignore[import-untyped]
from discord import FFmpegPCMAudio, PCMVolumeTransformer
from loguru import logger

from utils import StreamInfo, TrackRequestItem


class YouTubeSource:
    def __init__(self) -> None:
        user_agent = os.getenv(
            "SYMPHONY_YT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        )
        referer = os.getenv("SYMPHONY_YT_REFERER", "https://www.youtube.com/")
        origin = os.getenv("SYMPHONY_YT_ORIGIN", "https://www.youtube.com")

        debug = os.getenv("SYMPHONY_YT_DEBUG", "0").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        self.ytdl_format_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "logtostderr": debug,
            "quiet": not debug,
            "no_warnings": not debug,
            "default_search": "auto",
            "source_address": "0.0.0.0",
            "geo_bypass": True,
            "extract_flat": False,
            "skip_download": True,
            "max_downloads": 1,
            "playlistend": 1,
            "http_headers": {
                "User-Agent": user_agent,
                "Accept-Language": os.getenv(
                    "SYMPHONY_YT_ACCEPT_LANGUAGE", "en-US,en;q=0.9"
                ),
                "Referer": referer,
                "Origin": origin,
            },
            "extractor_args": {
                "youtube": {
                    "player_client": [os.getenv("SYMPHONY_YT_PLAYER_CLIENT", "android")]
                }
            },
        }

        cookiefile = os.getenv("SYMPHONY_YT_COOKIEFILE")
        if cookiefile:
            self.ytdl_format_options["cookiefile"] = cookiefile

        cookies_from_browser = os.getenv("SYMPHONY_YT_COOKIES_FROM_BROWSER")
        if cookies_from_browser:
            self.ytdl_format_options["cookiesfrombrowser"] = cookies_from_browser

        self.ffmpeg_options = {
            "before_options": (
                "-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                "-reconnect_on_http_error 4xx,5xx -nostdin -loglevel warning "
                "-probesize 64k -analyzeduration 0 "
                f'-user_agent "{user_agent}" '
                f'-headers "Referer: {referer}\\r\\nOrigin: {origin}\\r\\n"'
            ),
            "options": "-vn -sn -dn -bufsize 512k",
        }

        self.ytdl = yt_dlp.YoutubeDL(self.ytdl_format_options)
        self._stream_cache: "OrderedDict[str, Tuple[StreamInfo, float]]" = OrderedDict()
        self._cache_ttl = int(os.getenv("SYMPHONY_STREAM_CACHE_TTL", "900"))  # seconds
        self._cache_max_entries = 128
        self._inflight: Dict[str, asyncio.Task[StreamInfo]] = {}

    async def search(self, query: str) -> TrackRequestItem:
        loop = asyncio.get_running_loop()

        try:
            if not query.startswith("http"):
                query = f"ytsearch:{query}"

            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(query, download=False)
            )

            if "entries" in data:
                entries = data.get("entries") or []
                if not entries:
                    raise ValueError("No tracks found on YouTube for this query")
                data = entries[0]

            track = TrackRequestItem(
                id=data.get("id", ""),
                title=data.get("title", "Unknown"),
                url=data.get("webpage_url", ""),
                length=data.get("duration", 0),
                provider="YouTube",
                thumbnail=data.get("thumbnail"),
                stream_bitrate=data.get("abr"),
            )
            return track

        except Exception as exc:
            if isinstance(exc, yt_dlp.utils.DownloadError):
                error_text = str(exc)
                if "403" in error_text or "forbidden" in error_text.lower():
                    logger.error(
                        f"youTube 403 while searching: {query} :: {error_text}"
                    )
                    raise ValueError(
                        "❌ YouTube rejected the request (403 Forbidden). This is usually bot/consent enforcement rather than an IP ban. "
                        "Try updating yt-dlp and/or providing cookies (see README troubleshooting)."
                    ) from exc
                if any(
                    token in error_text.lower()
                    for token in ("404", "not found", "unavailable")
                ):
                    logger.error(f"youTube video not found: {query}")
                    raise ValueError(
                        "❌ YouTube video not found. The link may be invalid or the video may have been removed."
                    ) from exc
                logger.error(f"download error while searching YouTube: {exc}")
                raise ValueError(f"❌ Error accessing YouTube: {error_text}") from exc
            logger.error(f"error searching YouTube: {exc}")
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
    ) -> PCMVolumeTransformer:
        stream = stream_info or await self.resolve_stream(track)

        audio_source = FFmpegPCMAudio(
            stream.stream_url,
            **self.ffmpeg_options,  # type: ignore[arg-type]
        )
        return PCMVolumeTransformer(audio_source, volume=volume)

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
            if isinstance(exc, yt_dlp.utils.DownloadError):
                error_text = str(exc)
                if "403" in error_text or "forbidden" in error_text.lower():
                    logger.error(
                        f"youTube 403 while resolving stream: {url} :: {error_text}"
                    )
                    raise ValueError(
                        "❌ YouTube rejected the request (403 Forbidden). This is usually bot/consent enforcement rather than an IP ban. "
                        "Try updating yt-dlp and/or providing cookies (see README troubleshooting)."
                    ) from exc
                if any(
                    token in error_text.lower()
                    for token in ("404", "not found", "unavailable")
                ):
                    logger.error(f"youTube audio source not found for url: {url}")
                    raise ValueError("❌ YouTube video is no longer available") from exc
                logger.error(f"download error getting audio stream for {url}: {exc}")
                raise ValueError(
                    f"❌ Error getting audio from YouTube: {error_text}"
                ) from exc
            logger.error(f"unexpected error getting audio stream for {url}: {exc}")
            raise

    def _extract_stream_url(self, data: dict) -> str:
        # Prefer the format yt-dlp actually selected for "bestaudio".
        requested_formats = data.get("requested_formats")
        formats = []
        if isinstance(requested_formats, list):
            formats.extend(requested_formats)

        formats.extend(data.get("formats") or [])
        audio_formats = [
            fmt
            for fmt in formats
            if fmt.get("acodec") and fmt.get("acodec") != "none" and fmt.get("url")
        ]

        if not audio_formats:
            if data.get("url"):
                return data["url"]
            raise ValueError("No valid audio stream found for YouTube track")

        audio_formats.sort(key=lambda fmt: fmt.get("abr", 0), reverse=True)
        return audio_formats[0]["url"]

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

    def _extract_url_expiry(self, stream_url: str) -> Optional[float]:
        try:
            parsed = urlparse(stream_url)
            query = parse_qs(parsed.query)
            expires = query.get("expire")
            if not expires or not expires[0]:
                return None
            return float(int(expires[0]))
        except Exception:
            return None

    def _remember_stream(self, cache_key: str, stream: StreamInfo) -> None:
        now = time()
        ttl_expires_at = now + self._cache_ttl

        # If the signed googlevideo URL has its own expiry, honor it.
        url_expires_at = self._extract_url_expiry(stream.stream_url)
        if url_expires_at:
            # Keep a small safety margin to avoid starting playback with a near-expiry URL.
            url_expires_at = max(now, url_expires_at - 60)
            expires_at = min(ttl_expires_at, url_expires_at)
        else:
            expires_at = ttl_expires_at

        self._stream_cache[cache_key] = (stream, expires_at)
        self._stream_cache.move_to_end(cache_key)
        if len(self._stream_cache) > self._cache_max_entries:
            self._stream_cache.popitem(last=False)
