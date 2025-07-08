import asyncio

import discord
import yt_dlp
from loguru import logger

from utils import TrackRequestItem


class SoundCloudSource:
    def __init__(self):
        self.ytdl_format_options = {
            "format": "bestaudio[ext!=m3u8]/best[ext!=m3u8]",  # Avoid HLS streams
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
            "before_options": "-re -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -protocol_whitelist file,http,https,tcp,tls,crypto",
            "options": "-vn -bufsize 1024k",
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
                thumbnail=data.get("thumbnail", None),
            )

        except Exception as e:
            if isinstance(e, yt_dlp.utils.DownloadError):
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
        loop = asyncio.get_event_loop()

        try:
            data = await loop.run_in_executor(
                None, lambda: self.ytdl.extract_info(track.url, download=False)
            )

            audio_url = None

            if "formats" in data:
                formats = data["formats"]
                # Filter out HLS/m3u8 formats and prefer direct audio streams
                non_hls_formats = [
                    f
                    for f in formats
                    if f.get("url")
                    and not f.get("url", "").endswith(".m3u8")
                    and f.get("acodec")
                    and f.get("acodec") != "none"
                ]

                if non_hls_formats:
                    # Sort by audio quality
                    non_hls_formats.sort(key=lambda x: x.get("abr", 0), reverse=True)
                    audio_url = non_hls_formats[0]["url"]
                else:
                    # Fallback to any available format if no non-HLS found
                    audio_formats = [f for f in formats if f.get("acodec") != "none"]
                    if audio_formats:
                        audio_url = audio_formats[0]["url"]

            if not audio_url:
                audio_url = data.get("url")

            if not audio_url:
                raise ValueError("No valid audio stream found")

            if not audio_url.startswith("http"):
                raise ValueError("Invalid audio URL format")

            audio_source = discord.FFmpegPCMAudio(
                audio_url,
                **self.ffmpeg_options,
            )

            return discord.PCMVolumeTransformer(audio_source, volume=0.5)

        except Exception as e:
            logger.error(
                f"error getting SoundCloud audio source for {track.title}: {e}"
            )
            if isinstance(e, yt_dlp.utils.DownloadError):
                if "404" in str(e) or "not found" in str(e).lower():
                    raise ValueError("❌ SoundCloud track is no longer available")
                else:
                    raise ValueError(f"❌ Error accessing SoundCloud: {str(e)}")
            else:
                raise ValueError(f"❌ Failed to get audio source: {str(e)}")
