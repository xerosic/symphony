import asyncio
import os
from time import time

import discord
from discord import (
    Client,
    Member,
    PCMVolumeTransformer,
    TextChannel,
    VoiceClient,
    VoiceState,
)
from discord.ext import commands
from dotenv import load_dotenv
from loguru import logger

from sources.soundcloud import SoundCloudSource
from sources.youtube import YouTubeSource
from utils import (
    TrackQueueManager,
    TrackRequestItem,
    VolumeManager,
    escape_markdown,
    format_duration,
    get_cpu_usage,
    is_valid_url,
    is_vc_empty,
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot: Client = commands.Bot(command_prefix="!", intents=intents)


queue_manager = TrackQueueManager()
volume_manager = VolumeManager()
youtube_source = YouTubeSource()
soundcloud_source = SoundCloudSource()

bot_start_time = time()


async def get_audio_source(
    track: TrackRequestItem, guild_id: str = None
) -> PCMVolumeTransformer:
    volume = volume_manager.get_volume(guild_id)

    if track.provider == "YouTube":
        source = await youtube_source.get_audio_source(track)
        source.volume = volume
        return source
    elif track.provider == "SoundCloud":
        source = await soundcloud_source.get_audio_source(track)
        source.volume = volume
        return source
    else:
        # Default to YouTube
        source = await youtube_source.get_audio_source(track)
        source.volume = volume
        return source


async def get_track_from_query(query: str, provider: str = "auto") -> TrackRequestItem:
    if provider == "auto":
        if "soundcloud.com" in query:
            return await soundcloud_source.search(query)
        else:
            return await youtube_source.search(query)
    elif provider.lower() == "youtube":
        return await youtube_source.search(query)
    elif provider.lower() == "soundcloud":
        return await soundcloud_source.search(query)
    else:
        # Default to YouTube
        return await youtube_source.search(query)


async def play_next(
    guild: discord.Guild, voice_client: VoiceClient, channel: TextChannel
):
    if not voice_client.is_playing() and not voice_client.is_paused():
        next_track = queue_manager.get_next(str(guild.id))
        if next_track:
            try:
                source = await get_audio_source(next_track, str(guild.id))
                voice_client.play(
                    source,
                    after=lambda e: bot.loop.create_task(
                        play_next(guild, voice_client, channel)
                    ),
                )

                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{escape_markdown(next_track.title)}**",
                    color=0x1DB954,
                )
                embed.add_field(
                    name="⏱️ Duration",
                    value=format_duration(next_track.length),
                    inline=True,
                )
                embed.add_field(
                    name="📡 Source", value=next_track.provider, inline=True
                )
                embed.add_field(
                    name="🔗 URL", value=f"[Click here]({next_track.url})", inline=True
                )

                if hasattr(next_track, "thumbnail") and next_track.thumbnail:
                    embed.set_thumbnail(url=next_track.thumbnail)

                embed.set_footer(
                    text=f"Requested by {channel.guild.me.name}",
                    icon_url=channel.guild.me.avatar.url
                    if channel.guild.me.avatar
                    else None,
                )

                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"error playing next track: {e}.")
                await channel.send(f"❌ An error occurred: {str(e)}")


@bot.event
async def on_ready():
    logger.info(f"logged in as {bot.user}.")

    activity = discord.Activity(
        type=discord.ActivityType.listening, name="your music requests 🎵"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

    for guild in bot.guilds:
        if guild.voice_client and not guild.voice_client.self_deaf:
            await guild.change_voice_state(
                channel=guild.voice_client.channel, self_deaf=True
            )

    try:
        synced = await bot.tree.sync()
        logger.success(f"synced {len(synced)} command(s).")
    except Exception as e:
        logger.error(f"failed to sync commands: {e}.")


@bot.event
async def on_voice_state_update(member: Member, before: VoiceState, after: VoiceState):
    if member == bot.user:
        return

    voice_client = member.guild.voice_client
    if not voice_client:
        return

    if (
        before.channel == voice_client.channel and after.channel != voice_client.channel
    ):  # check if some1 left
        if is_vc_empty(voice_client):
            logger.debug(
                f"all users left voice channel in guild {member.guild.id}, disconnecting bot"
            )

            # Clean up and disconnect
            queue_manager.drop_queue(str(member.guild.id))
            await voice_client.disconnect()


@bot.tree.command(name="play", description="Play a song from YouTube or SoundCloud")
async def play(interaction: discord.Interaction, query: str, provider: str):
    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel!", ephemeral=True
        )
        return

    if is_valid_url(query):  # handle links
        if provider.lower() not in ["youtube", "soundcloud"]:
            await interaction.response.send_message(
                "❌ Invalid provider! Use 'youtube' or 'soundcloud'.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🔗 Playing from **{provider.capitalize()}**: *{query}*"
        )
    else:  # handle search queries
        await interaction.response.send_message(f"🔍 **Searching for:** *{query}*")

    voice_channel = interaction.user.voice.channel

    # Start both operations concurrently
    track_task = asyncio.create_task(get_track_from_query(query, provider))

    try:
        voice_client = await voice_channel.connect()
    except discord.ClientException:
        voice_client = interaction.guild.voice_client

    try:
        track = await track_task

        if voice_client.is_playing() or voice_client.is_paused():
            queue_manager.append(str(interaction.guild.id), track)

            embed = discord.Embed(
                title="🎵 Added to Queue",
                description=f"**{track.title}**",
                color=0x3498DB,
            )
            embed.add_field(
                name="⏱️ Duration", value=format_duration(track.length), inline=True
            )
            embed.add_field(name="📡 Source", value=track.provider, inline=True)
            embed.add_field(
                name="⏬ Position",
                value=f"{queue_manager.get_queue_length(str(interaction.guild.id))}",
                inline=True,
            )

            if hasattr(track, "thumbnail") and track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)

            embed.set_footer(
                text=f"Requested by {interaction.user.name}",
                icon_url=interaction.user.avatar.url
                if interaction.user.avatar
                else None,
            )

            await interaction.followup.send(embed=embed)
        else:
            # Start playing immediately with a placeholder, then swap to real audio
            source = await get_audio_source(track, str(interaction.guild.id))
            voice_client.play(
                source,
                after=lambda e: bot.loop.create_task(
                    play_next(interaction.guild, voice_client, interaction.channel)
                ),
            )

            embed = discord.Embed(
                title="🎵 Now Playing",
                description=f"**{escape_markdown(track.title)}**",
                color=0x1DB954,
            )
            embed.add_field(
                name="Duration", value=format_duration(track.length), inline=True
            )
            embed.add_field(name="Source", value=track.provider, inline=True)
            embed.add_field(name="URL", value=f"[Click here]({track.url})", inline=True)

            if hasattr(track, "thumbnail") and track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)

            embed.set_footer(
                text=f"Requested by {interaction.user.name}",
                icon_url=interaction.user.avatar.url
                if interaction.user.avatar
                else None,
            )

            await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"error in play command: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}")


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel!", ephemeral=True
        )
        return

    if not voice_client:
        await interaction.response.send_message(
            "🔇 I'm not playing anything!", ephemeral=True
        )
        return

    if not voice_client.is_playing():
        await interaction.response.send_message(
            "🔇 Nothing is playing!", ephemeral=True
        )
        return

    voice_client.stop()
    await interaction.response.send_message("⏭️ Skipped to next song")


@bot.tree.command(name="volume", description="Set the volume (0-100)")
async def volume(interaction: discord.Interaction, volume: int):
    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel!", ephemeral=True
        )
        return

    voice_client: VoiceClient = interaction.guild.voice_client
    if not voice_client:
        await interaction.response.send_message(
            "🔇 I'm not connected to a voice channel!", ephemeral=True
        )
        return

    if volume < 0 or volume > 100:
        await interaction.response.send_message(
            "❌ Volume must be between 0 and 100.", ephemeral=True
        )
        return

    volume_manager.set_volume(str(interaction.guild.id), volume / 100.0)

    if voice_client.source and hasattr(voice_client.source, "volume"):
        voice_client.source.volume = volume / 100.0

    await interaction.response.send_message(f"🔊 Volume set to {volume}%")


@bot.tree.command(name="stop", description="Stop playing music and clear queue")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        queue_manager.drop_queue(str(interaction.guild.id))
        await interaction.response.send_message("⏹️ Stopped playing and cleared queue")
    else:
        await interaction.response.send_message("🔇 Nothing is playing!")


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused")
    else:
        await interaction.response.send_message("🔇 Nothing is playing!")


@bot.tree.command(name="resume", description="Resume playing")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed")
    else:
        await interaction.response.send_message("🔇 Nothing is paused!")


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client:
        queue_manager.drop_queue(str(interaction.guild.id))
        await voice_client.disconnect()
        await interaction.response.send_message(
            "👋 Left the voice channel and cleared queue"
        )
    else:
        await interaction.response.send_message("🔇 I'm not in a voice channel!")


@bot.tree.command(name="stats", description="Get bot statistics")
async def stats(interaction: discord.Interaction):
    total_guilds = len(bot.guilds)
    total_users = sum(len(guild.members) for guild in bot.guilds)

    uptime_seconds = int(time() - bot_start_time)
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60
    uptime_secs = uptime_seconds % 60
    uptime_str = f"{uptime_hours}h {uptime_minutes}m {uptime_secs}s"

    # Get bot latency in milliseconds
    ping = round(bot.latency * 1000, 2)

    embed = discord.Embed(
        title="📈 Bot Statistics",
        color=0x3498DB,
    )
    embed.add_field(name="🏠 Total Guilds", value=str(total_guilds), inline=True)
    embed.add_field(name="👥 Total Users", value=str(total_users), inline=True)
    embed.add_field(name="💻 CPU Usage", value=f"{get_cpu_usage():.2f}%", inline=True)
    embed.add_field(name="📶 Ping", value=f"{ping}ms", inline=True)
    embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)

    embed.set_footer(
        text="🎵 Powered by https://github.com/xerosic/symphony 🎵",
        icon_url=bot.user.avatar.url if bot.user.avatar else None,
    )

    await interaction.response.send_message(embed=embed)


load_dotenv()
try:
    bot.run(token=os.getenv("DISCORD_TOKEN"), reconnect=True)
except discord.LoginFailure as e:
    logger.critical(f"failed to login: {e}. Please check your token.")
except discord.HTTPException as e:
    logger.critical(
        f"failed to connect to Discord: {e}. Please check your internet connection."
    )
except KeyboardInterrupt:
    logger.info("stopping gracefully...")
    bot.close()
