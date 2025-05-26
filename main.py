import os

import discord
from discord import Member, PCMVolumeTransformer, TextChannel, VoiceClient, VoiceState
from discord.ext import commands
from dotenv import load_dotenv
from loguru import logger

from sources.soundcloud import SoundCloudSource
from sources.youtube import YouTubeSource
from utils import (
    TrackQueueManager,
    TrackRequestItem,
    is_valid_url,
    is_vc_empty,
    escape_markdown,
    format_duration,
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)


queue_manager = TrackQueueManager()
youtube_source = YouTubeSource()
soundcloud_source = SoundCloudSource()


async def get_audio_source(track: TrackRequestItem) -> PCMVolumeTransformer:
    if track.provider == "YouTube":
        return await youtube_source.get_audio_source(track)
    elif track.provider == "SoundCloud":
        return await soundcloud_source.get_audio_source(track)
    else:
        # Default to YouTube
        return await youtube_source.get_audio_source(track)


async def play_next(
    guild: discord.Guild, voice_client: VoiceClient, channel: TextChannel
):
    if not voice_client.is_playing() and not voice_client.is_paused():
        next_track = queue_manager.get_next(str(guild.id))
        if next_track:
            try:
                source = await get_audio_source(next_track)
                voice_client.play(
                    source,
                    after=lambda e: bot.loop.create_task(
                        play_next(guild, voice_client, channel)
                    ),
                )

                # Create embed with track info
                embed = discord.Embed(
                    title="🎵 Now Playing",
                    description=f"**{escape_markdown(next_track.title)}**",
                    color=0x1DB954,
                )
                embed.add_field(
                    name="Duration",
                    value=format_duration(next_track.length),
                    inline=True,
                )
                embed.add_field(name="Source", value=next_track.provider, inline=True)
                embed.add_field(
                    name="URL", value=f"[Click here]({next_track.url})", inline=True
                )

                if hasattr(next_track, "thumbnail") and next_track.thumbnail:
                    embed.set_thumbnail(url=next_track.thumbnail)

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


async def get_track_from_query(query: str, provider: str = "auto") -> TrackRequestItem:
    """Get a track from a query, automatically detecting or using specified provider"""

    if provider == "auto":
        # Auto-detect provider based on URL or default to YouTube
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
        await interaction.response.send_message(f"🔍 Searching for: *{query}*")

    voice_channel = interaction.user.voice.channel

    try:
        voice_client = await voice_channel.connect()
    except discord.ClientException:
        voice_client = interaction.guild.voice_client

    try:
        track = await get_track_from_query(query, provider)

        if voice_client.is_playing() or voice_client.is_paused():
            queue_manager.append(str(interaction.guild.id), track)

            # Create embed for queue addition
            embed = discord.Embed(
                title="🎵 Added to Queue",
                description=f"**{track.title}**",
                color=0x3498DB,
            )
            embed.add_field(
                name="Duration", value=format_duration(track.length), inline=True
            )
            embed.add_field(name="Source", value=track.provider, inline=True)
            embed.add_field(
                name="Position",
                value=f"{queue_manager.get_queue_length(str(interaction.guild.id))}",
                inline=True,
            )

            if hasattr(track, "thumbnail") and track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)

            await interaction.followup.send(embed=embed)
        else:
            source = await get_audio_source(track)
            voice_client.play(
                source,
                after=lambda e: bot.loop.create_task(
                    play_next(interaction.guild, voice_client, interaction.channel)
                ),
            )

            # Create embed for now playing
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


load_dotenv()
try:
    bot.run(token=os.getenv("DISCORD_TOKEN"), reconnect=True)
except discord.LoginFailure as e:
    logger.error(f"failed to login: {e}. Please check your token.")
