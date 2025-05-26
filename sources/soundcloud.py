import asyncio

import discord
import yt_dlp
from loguru import logger

from utils import TrackRequestItem


class SoundCloudSource:
    def __init__(self):
        self.ytdl_format_options = {
            "format": "bestaudio",
            "noplaylist": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "logtostderr": False,
            "quiet": True,
            "no_warnings": True,
            "default_search": "auto",
            "source_address": "0.0.0.0",
            "geo_bypass": True,
        }

        self.ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn",
        }

        self.ytdl = yt_dlp.YoutubeDL(self.ytdl_format_options)

    async def search(self, query: str) -> TrackRequestItem:
        loop = asyncio.get_event_loop()

        try:
            # If it's not a SoundCloud URL, search SoundCloud
            if not query.startswith("http"):
                query = f"scsearch:{query}"

            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(query, download=False)
            )

            if "entries" in data:
                if not data["entries"]:  # No search results found
                    raise ValueError("No tracks found on SoundCloud for this query")
                data = data["entries"][0]

            return TrackRequestItem(
                id=data.get("id", ""),
                title=data.get("title", "Unknown"),
                url=data.get("webpage_url", ""),
                length=data.get("duration", 0),
                provider="SoundCloud",
                thumbnail=data.get("thumbnail", None)
            )

        except Exception as e:
            if isinstance(e, yt_dlp.utils.DownloadError):
                # Check if it's a 404 error (track not found)
                if "404" in str(e) or "not found" in str(e).lower():
                    logger.error(f"soundCloud track not found: {query}")
                    raise ValueError(
                        "❌ SoundCloud track not found. The link may be invalid or the track may have been removed."
                    )
                else:
                    logger.error(f"download error while searching SoundCloud: {e}")
                    raise ValueError(f"❌ Error accessing SoundCloud: {str(e)}")
            else:
                logger.error(f"error searching SoundCloud: {e}")
                raise e

    async def get_audio_source(
        self, track: TrackRequestItem
    ) -> discord.PCMVolumeTransformer:
        """Get the audio source for a track"""
        loop = asyncio.get_event_loop()

        try:
            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(track.url, download=False)
            )

            audio_url = data["url"]

            audio_source = discord.FFmpegPCMAudio(
                audio_url,
                **self.ffmpeg_options,
            )

            return discord.PCMVolumeTransformer(audio_source, volume=0.5)

        except Exception as e:
            if isinstance(e, yt_dlp.utils.DownloadError):
                if "404" in str(e) or "not found" in str(e).lower():
                    logger.error(
                        f"soundCloud audio source not found for: {track.title}"
                    )
                    raise ValueError("❌ SoundCloud track is no longer available")
                else:
                    logger.error(
                        f"download error getting audio source for {track.title}: {e}"
                    )
                    raise ValueError(
                        f"❌ Error getting audio from SoundCloud: {str(e)}"
                    )
            else:
                logger.error(f"error getting audio source for {track.title}: {e}")
                raise e
