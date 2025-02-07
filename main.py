import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
from typing import Optional
import os
from dotenv import load_dotenv
from loguru import logger


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


class MusicQueue:
    def __init__(self):
        self.queues = {}

    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = []
        return self.queues[guild_id]

    def add_to_queue(self, guild_id, item):
        self.get_queue(guild_id).append(item)

    def remove_from_queue(self, guild_id):
        queue = self.get_queue(guild_id)
        if queue:
            return queue.pop(0)
        return None

    def clear_queue(self, guild_id):
        self.queues[guild_id] = []


queue_system = MusicQueue()

ytdl_format_options = {
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

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def create_source(cls, search: str, *, loop=None):
        loop = loop or asyncio.get_event_loop()

        try:
            if not search.startswith("http"):
                search = f"ytsearch:{search}"

            data = await loop.run_in_executor(
                None, lambda: ytdl.extract_info(search, download=False)
            )

            if "entries" in data:
                data = data["entries"][0]

            audio_url = data["url"]

            audio_source = discord.FFmpegPCMAudio(
                audio_url,
                **ffmpeg_options,
            )

            return cls(audio_source, data=data)

        except Exception as e:
            logger.error(f"Error in create_source: {e}")
            raise e


@bot.event
async def on_ready():
    logger.info(f"logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.success(f"synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"failed to sync commands: {e}")


async def play_next(guild, voice_client, channel):
    if not voice_client.is_playing() and not voice_client.is_paused():
        next_song = queue_system.remove_from_queue(guild.id)
        if next_song:
            try:
                source = await YTDLSource.create_source(next_song, loop=bot.loop)
                voice_client.play(
                    source,
                    after=lambda e: bot.loop.create_task(
                        play_next(guild, voice_client, channel)
                    ),
                )
                await channel.send(f"🎵 Now playing: **{source.title}**")
            except Exception as e:
                await channel.send(f"❌ An error occurred: {str(e)}")


@bot.tree.command(
    name="play", description="Play a song from YouTube URL or search term"
)
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel!", ephemeral=True
        )
        return

    voice_channel = interaction.user.voice.channel

    try:
        voice_client = await voice_channel.connect()
    except discord.ClientException:
        voice_client = interaction.guild.voice_client

    await interaction.response.send_message(f"🔍 Searching for: {query}")

    try:
        if voice_client.is_playing() or voice_client.is_paused():
            queue_system.add_to_queue(interaction.guild.id, query)
            await interaction.channel.send(f"🎵 Added to queue: **{query}**")
        else:
            source = await YTDLSource.create_source(query, loop=bot.loop)
            voice_client.play(
                source,
                after=lambda e: bot.loop.create_task(
                    play_next(interaction.guild, voice_client, interaction.channel)
                ),
            )
            await interaction.channel.send(f"🎵 Now playing: **{source.title}**")

    except Exception as e:
        await interaction.channel.send(
            f"❌ An error occurred: {str(e)}", ephemeral=True
        )


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not interaction.user.voice:
        await interaction.response.send_message(
            "❌ You need to be in a voice channel to add to the queue!", ephemeral=True
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


@bot.tree.command(name="stop", description="Stop playing music")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await interaction.response.send_message("⏹️ Stopped playing")
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
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Left the voice channel")
    else:
        await interaction.response.send_message("🔇 I'm not in a voice channel!")


load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))
