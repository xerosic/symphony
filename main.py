from __future__ import annotations

import asyncio
import os
from time import time
from typing import Callable, Dict, Optional, Protocol, Sequence, Set, cast

import discord
from discord import (
    Member,
    PCMVolumeTransformer,
    VoiceClient,
    VoiceState,
    app_commands,
)
from discord.ext import commands
from discord.abc import Messageable
from dotenv import load_dotenv
from loguru import logger

from sources.soundcloud import SoundCloudSource
from sources.youtube import YouTubeSource
from utils import (
    TrackQueueManager,
    TrackRequestItem,
    StreamInfo,
    VolumeManager,
    escape_markdown,
    format_duration,
    get_cpu_usage,
    is_vc_empty,
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot: commands.Bot = commands.Bot(command_prefix="!", intents=intents)

queue_manager = TrackQueueManager()
volume_manager = VolumeManager()
youtube_source = YouTubeSource()
soundcloud_source = SoundCloudSource()

bot_start_time = time()
prefetch_tasks: Set[asyncio.Task[StreamInfo]] = set()
SYSTEM_FOOTER_TEXT = "🎵 Symphony • https://github.com/xerosic/symphony"


class AudioProvider(Protocol):
    async def search(self, query: str) -> TrackRequestItem: ...

    async def resolve_stream(self, track: TrackRequestItem) -> StreamInfo: ...

    async def get_audio_source(
        self,
        track: TrackRequestItem,
        volume: float,
        stream_info: Optional[StreamInfo] = None,
    ) -> PCMVolumeTransformer: ...


PROVIDER_MAP: Dict[str, AudioProvider] = {
    "youtube": youtube_source,
    "soundcloud": soundcloud_source,
}


def normalize_provider_name(provider: str, query: Optional[str] = None) -> str:
    base = provider.lower()
    if base == "auto":
        if query and "soundcloud.com" in query.lower():
            return "soundcloud"
        return "youtube"
    if base not in PROVIDER_MAP:
        return "youtube"
    return base


def get_provider(provider_name: str) -> AudioProvider:
    return PROVIDER_MAP.get(provider_name.lower(), youtube_source)


def schedule_stream_prefetch(track: TrackRequestItem) -> None:
    provider = get_provider(track.provider)
    task = asyncio.create_task(provider.resolve_stream(track))
    prefetch_tasks.add(task)

    def _cleanup(done_task: asyncio.Task[StreamInfo]) -> None:
        prefetch_tasks.discard(done_task)
        if done_task.cancelled():
            return
        exc = done_task.exception()
        if exc:
            logger.error(f"prefetch error for {track.title}: {exc}")

    task.add_done_callback(_cleanup)


def build_track_embed(
    *,
    track: TrackRequestItem,
    title: str,
    color: int,
    requester_name: Optional[str] = None,
    requester_avatar: Optional[str] = None,
    extra_fields: Optional[Sequence[tuple[str, str]]] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"**{escape_markdown(track.title)}**",
        color=color,
    )
    embed.add_field(name="⏱️ Duration", value=format_duration(track.length), inline=True)
    embed.add_field(name="📡 Source", value=track.provider, inline=True)
    embed.add_field(name="🔗 URL", value=f"[Open]({track.url})", inline=True)

    if extra_fields:
        for name, value in extra_fields:
            embed.add_field(name=name, value=value, inline=True)

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    resolved_name = requester_name or track.requested_by_name or "Unknown"
    resolved_avatar = requester_avatar or track.requested_by_avatar
    footer_text = f"Requested by:  {resolved_name}"
    embed.set_footer(text=footer_text, icon_url=resolved_avatar)
    return embed


def build_error_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=0xE74C3C,
    )
    embed.set_footer(text=SYSTEM_FOOTER_TEXT)
    return embed


async def send_interaction_error(
    interaction: discord.Interaction,
    *,
    title: str,
    description: str,
    ephemeral: bool = True,
) -> None:
    embed = build_error_embed(title, description)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def send_channel_error(
    channel: Messageable,
    *,
    title: str,
    description: str,
) -> None:
    await channel.send(embed=build_error_embed(title, description))


def after_playback_callback(
    guild: discord.Guild,
    voice_client: VoiceClient,
    channel: Messageable,
) -> Callable[[Optional[Exception]], None]:
    def _callback(error: Optional[Exception]) -> None:
        if error:
            logger.error(f"playback error: {error}")
        asyncio.run_coroutine_threadsafe(
            play_next(guild, voice_client, channel),
            bot.loop,
        )

    return _callback


async def ensure_voice_connection(interaction: discord.Interaction) -> VoiceClient:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, Member):
        raise ValueError("This command can only be used inside a server.")

    user_voice_state = interaction.user.voice
    if not user_voice_state or not user_voice_state.channel:
        raise ValueError("You need to be connected to a voice channel.")

    voice_client = cast(Optional[VoiceClient], guild.voice_client)
    if voice_client and voice_client.channel == user_voice_state.channel:
        return voice_client

    try:
        if voice_client:
            await voice_client.move_to(user_voice_state.channel)
            return voice_client

        new_client: VoiceClient = await user_voice_state.channel.connect()
        if new_client.channel:
            await guild.change_voice_state(channel=new_client.channel, self_deaf=True)
        return new_client
    except discord.ClientException as exc:
        logger.error(f"failed to connect to voice: {exc}")
        raise ValueError("Unable to join the voice channel.") from exc


async def get_audio_source(
    track: TrackRequestItem,
    guild_id: Optional[str],
    stream_info: Optional[StreamInfo] = None,
) -> PCMVolumeTransformer:
    provider = get_provider(track.provider)
    volume = volume_manager.get_volume(guild_id)
    return await provider.get_audio_source(track, volume, stream_info)


async def get_track_from_query(query: str, provider: str = "auto") -> TrackRequestItem:
    provider_key = normalize_provider_name(provider, query)
    provider_instance = get_provider(provider_key)
    return await provider_instance.search(query)


async def play_next(
    guild: discord.Guild,
    voice_client: VoiceClient,
    channel: Messageable,
) -> None:
    if voice_client.is_playing() or voice_client.is_paused():
        return

    next_track = queue_manager.get_next(str(guild.id))
    if not next_track:
        if is_vc_empty(voice_client):
            queue_manager.drop_queue(str(guild.id))
            await voice_client.disconnect(force=False)
        return

    try:
        provider = get_provider(next_track.provider)
        stream_info = await provider.resolve_stream(next_track)
        source = await get_audio_source(next_track, str(guild.id), stream_info)
        voice_client.play(
            source,
            after=after_playback_callback(guild, voice_client, channel),
        )

        bot_user = bot.user
        bot_member = guild.me
        if bot_member is None and bot_user:
            bot_member = guild.get_member(bot_user.id)
        fallback_name = bot_member.display_name if bot_member else guild.name
        fallback_avatar = (
            bot_member.avatar.url if bot_member and bot_member.avatar else None
        )
        requester_name = next_track.requested_by_name or fallback_name
        requester_avatar = next_track.requested_by_avatar or fallback_avatar
        embed = build_track_embed(
            track=next_track,
            title="🎵  Now Playing",
            color=0x1DB954,
            requester_name=requester_name,
            requester_avatar=requester_avatar,
        )
        await channel.send(embed=embed)
    except Exception as exc:
        logger.error(f"error playing next track: {exc}")
        await send_channel_error(
            channel,
            title="❌ Playback Error",
            description=str(exc),
        )
        await play_next(guild, voice_client, channel)


@bot.event
async def on_ready() -> None:
    logger.info(f"logged in as {bot.user}.")
    activity = discord.Activity(
        type=discord.ActivityType.listening, name="your music requests 🎵"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

    try:
        synced = await bot.tree.sync()
        logger.success(f"synced {len(synced)} command(s).")
    except Exception as exc:
        logger.error(f"failed to sync commands: {exc}")


@bot.event
async def on_voice_state_update(member: Member, before: VoiceState, after: VoiceState):
    if member == bot.user:
        return

    voice_client = member.guild.voice_client
    if not isinstance(voice_client, VoiceClient):
        return

    if before.channel == voice_client.channel and after.channel != voice_client.channel:
        if is_vc_empty(voice_client):
            logger.debug(
                f"all users left voice channel in guild {member.guild.id}, disconnecting bot"
            )
            queue_manager.drop_queue(str(member.guild.id))
            await voice_client.disconnect(force=False)


@bot.tree.command(name="play", description="Play a song from YouTube or SoundCloud")
@app_commands.choices(
    provider=[
        app_commands.Choice(name="YouTube", value="youtube"),
        app_commands.Choice(name="SoundCloud", value="soundcloud"),
        app_commands.Choice(name="Auto", value="auto"),
    ]
)
async def play(interaction: discord.Interaction, query: str, provider: str = "auto"):
    if not interaction.guild or not isinstance(interaction.user, Member):
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel!", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        guild_id = str(interaction.guild.id)
        voice_client = await ensure_voice_connection(interaction)
        track = await get_track_from_query(query, provider)
        schedule_stream_prefetch(track)

        channel = interaction.channel
        if channel is None or not isinstance(channel, Messageable):
            raise RuntimeError("Unable to determine the text channel for this interaction.")

        requester_name = interaction.user.display_name
        requester_avatar = (
            interaction.user.avatar.url if interaction.user.avatar else None
        )

        track.requested_by_name = requester_name
        track.requested_by_avatar = requester_avatar

        if voice_client.is_playing() or voice_client.is_paused():
            queue_manager.append(guild_id, track)
            position = queue_manager.get_queue_length(guild_id)
            embed = build_track_embed(
                track=track,
                title="🎵 Added to Queue",
                color=0x3498DB,
                requester_name=requester_name,
                requester_avatar=requester_avatar,
                extra_fields=[("⏬ Position", str(position))],
            )
            await interaction.followup.send(embed=embed)
            return

        provider_instance = get_provider(track.provider)
        stream_info = await provider_instance.resolve_stream(track)
        source = await get_audio_source(track, guild_id, stream_info)
        voice_client.play(
            source,
            after=after_playback_callback(interaction.guild, voice_client, channel),
        )

        embed = build_track_embed(
            track=track,
            title="🎵  Now Playing",
            color=0x1DB954,
            requester_name=requester_name,
            requester_avatar=requester_avatar,
        )
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        logger.error(f"error handling /play: {exc}")
        await send_interaction_error(
            interaction,
            title="Unable to play track",
            description=str(exc),
        )


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(
            "🔇 Nothing is playing!", ephemeral=True
        )
        return

    voice_client.stop()
    await interaction.response.send_message("⏭️ Skipped to next song")


@bot.tree.command(name="volume", description="Set the volume (0-100)")
async def volume(interaction: discord.Interaction, volume: int):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if not voice_client:
        await interaction.response.send_message(
            "🔇 I'm not connected to a voice channel!", ephemeral=True
        )
        return

    if not 0 <= volume <= 100:
        await interaction.response.send_message(
            "❌ Volume must be between 0 and 100.", ephemeral=True
        )
        return

    guild_id = str(interaction.guild.id)
    volume_value = volume / 100.0
    volume_manager.set_volume(guild_id, volume_value)

    source = getattr(voice_client, "source", None)
    if source and hasattr(source, "volume"):
        source.volume = volume_value

    await interaction.response.send_message(f"🔊 Volume set to {volume}%")


@bot.tree.command(name="stop", description="Stop playing music and clear queue")
async def stop(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if not voice_client:
        await interaction.response.send_message(
            "🔇 Nothing is playing!", ephemeral=True
        )
        return

    voice_client.stop()
    queue_manager.drop_queue(str(interaction.guild.id))
    await interaction.response.send_message("⏹️ Stopped playing and cleared queue")


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused")
    else:
        await interaction.response.send_message(
            "🔇 Nothing is playing!", ephemeral=True
        )


@bot.tree.command(name="resume", description="Resume playing")
async def resume(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed")
    else:
        await interaction.response.send_message("🔇 Nothing is paused!", ephemeral=True)


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
        return

    voice_client = cast(Optional[VoiceClient], interaction.guild.voice_client)
    if voice_client:
        queue_manager.drop_queue(str(interaction.guild.id))
        await voice_client.disconnect(force=False)
        await interaction.response.send_message(
            "👋 Left the voice channel and cleared queue"
        )
    else:
        await interaction.response.send_message(
            "🔇 I'm not in a voice channel!", ephemeral=True
        )


@bot.tree.command(name="stats", description="Get bot statistics")
async def stats(interaction: discord.Interaction):
    uptime_seconds = int(time() - bot_start_time)
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60
    uptime_secs = uptime_seconds % 60
    uptime_str = f"{uptime_hours}h {uptime_minutes}m {uptime_secs}s"
    ping = round(bot.latency * 1000, 2)

    embed = discord.Embed(title="📈 Bot Statistics", color=0x3498DB)
    embed.add_field(name="🏠 Total Guilds", value=str(len(bot.guilds)), inline=True)
    embed.add_field(
        name="👥 Total Users",
        value=str(sum(len(guild.members) for guild in bot.guilds)),
        inline=True,
    )
    embed.add_field(name="💻 CPU Usage", value=f"{get_cpu_usage():.2f}%", inline=True)
    embed.add_field(name="📶 Ping", value=f"{ping}ms", inline=True)
    embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
    embed.add_field(
        name="🎛️ Prefetch Tasks",
        value=str(len(prefetch_tasks)),
        inline=True,
    )

    icon_url = bot.user.avatar.url if bot.user and bot.user.avatar else None
    embed.set_footer(
        text="Powered by https://github.com/xerosic/symphony 🎵",
        icon_url=icon_url,
    )

    await interaction.response.send_message(embed=embed)


@play.error
async def play_error_handler(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    logger.error(f"/play failed: {error}")
    description = "An unexpected error occurred."
    if isinstance(error, app_commands.CommandInvokeError) and error.original:
        description = str(error.original)
    await send_interaction_error(
        interaction,
        title="Play command error",
        description=description,
    )


load_dotenv()
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

try:
    bot.run(token=token, reconnect=True)
except discord.LoginFailure as exc:
    logger.critical(f"failed to login: {exc}. Please check your token.")
except discord.HTTPException as exc:
    logger.critical(
        f"failed to connect to Discord: {exc}. Please check your internet connection."
    )
except KeyboardInterrupt:
    logger.info("stopping gracefully...")
