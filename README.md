<div align="center">

# 🎼 Symphony 
### *No-fuss self-hostable Discord music bot*

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Docker](https://img.shields.io/badge/docker-supported-blue.svg)

*Bring YouTube and SoundCloud music directly in your favourite Discord server*

</div>

> [!NOTE]
> 🎉 **Symphony v1.0 is now available!** Featuring new audio sources (SoundCloud), more robust Queue system and more commands.

## ✨ Features

- 🎵 **Multi-platform support** - Play music from YouTube and SoundCloud
- 📊 **Bot statistics** - Monitor uptime, CPU usage, and server count
- 🔧 **Self-hostable** - Full control over your music bot
- 🐳 **Docker ready** - Easy deployment with pre-built images


## 🚀 Quick Start

### Option 1: Docker (Recommended)

Create a `docker-compose.yml` file:

```yaml
version: '3.8'
services:
  symphony-bot:
    image: ghcr.io/xerosic/symphony:latest
    container_name: symphony-bot
    environment:
      - DISCORD_TOKEN=YOUR_TOKEN_HERE
         # Optional (recommended if you hit YouTube 403 / age-gated / consent-gated videos)
         - SYMPHONY_YT_COOKIEFILE=/data/cookies.txt
         # Enable to get yt-dlp debug logs in container output
         # - SYMPHONY_YT_DEBUG=1
      volumes:
         # Put cookies at ./data/cookies.txt on the host
         - ./data:/data:ro
    restart: unless-stopped
```

Launch the bot:
```bash
docker-compose up -d
```

### Option 2: Run from Source

1. **Clone & Setup**
   ```bash
   git clone https://github.com/xerosic/symphony.git
   cd symphony
   mv .env.example .env
   ```

2. **Configure**
   - Edit `.env` file with your Discord bot token

3. **Install & Run**
   ```bash
   pip install -r requirements.txt
   python main.py
   ```

## 🎮 Commands

| Command   | Description                           | Usage                      |
| --------- | ------------------------------------- | -------------------------- |
| `/play`   | Play music from YouTube or SoundCloud | `/play <query> <provider>` |
| `/skip`   | Skip to the next song                 | `/skip`                    |
| `/pause`  | Pause current playback                | `/pause`                   |
| `/resume` | Resume paused playback                | `/resume`                  |
| `/stop`   | Stop playing and clear queue          | `/stop`                    |
| `/volume` | Set playback volume (0-100)           | `/volume <number>`         |
| `/leave`  | Disconnect from voice channel         | `/leave`                   |
| `/stats`  | Display bot statistics                | `/stats`                   |

## 🎯 Usage Examples

```
/play Never Gonna Give You Up youtube
/play https://www.youtube.com/watch?v=wJv52fsVjaM youtube
/play Chill beats soundcloud
/volume 75
/skip
```

## 🐳 Docker Images

Pre-built Docker images are automatically published to GitHub Container Registry:

- `ghcr.io/xerosic/symphony:latest` - Latest stable version
- `ghcr.io/xerosic/symphony:main` - Latest main branch

## 🛠️ Troubleshooting

### YouTube `403 Forbidden`

YouTube can return `403` even on residential IPs. This is typically automated-traffic enforcement (client fingerprint, missing cookies/consent, request patterns), not a simple IP ban.

- Update `yt-dlp` to the latest version (YouTube frequently changes internals): `pip install -U yt-dlp`
- If specific videos are age/consent gated, provide cookies:
   - Docker: mount `./data/cookies.txt` into the container and set `SYMPHONY_YT_COOKIEFILE=/data/cookies.txt`
   - Non-docker: `SYMPHONY_YT_COOKIEFILE=/path/to/cookies.txt`
   - Alternative: `SYMPHONY_YT_COOKIES_FROM_BROWSER=chrome` (also supports `firefox`, etc.)

If you enabled debug logs and see a warning about a **PO Token** (e.g. Android client requiring `GVS PO Token`), either:
- Switch the player client to `web` (default) or configure a fallback list with `SYMPHONY_YT_PLAYER_CLIENTS=web,ios`
- Or provide a token via `SYMPHONY_YT_PO_TOKEN` (advanced; see yt-dlp PO Token guide)


## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
