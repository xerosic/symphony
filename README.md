# 🎼 Symphony - No fuss self-hostable Discord musicbot 

Symphony is a simple self-hostable Discord musicbot that can play music from YouTube and Soundcloud.

## Installation

### Option 1: Using Pre-built Docker Image (Recommended)

1. Create a `docker-compose.yml` file:
```yaml
version: '3.8'
services:
  symphony-bot:
    image: ghcr.io/xerosic/symphony:latest
    container_name: symphony-bot
    environment:
      - DISCORD_TOKEN=YOUR_TOKEN_HERE
    restart: unless-stopped
```

2. Run the bot:
```bash
docker-compose up -d
```

### Option 2: Run from Source

1. Clone the repository
2. Rename the `.env.example` file to `.env`

```bash
cp .env.example .env
```

3. Modify the `.env` file with your Discord bot token
4. Install dependencies:

```bash
pip install -r requirements.txt
```
5. Run the bot:

```bash
python main.py
```

## Usage

_The bot supports slash commands_

- `/play <query> <provider>`: Play a song from YouTube or Soundcloud (both URL and query are supported), where `<provider>` can be `youtube` or `soundcloud`.
  
- `/skip`: Skip the current song
- 
- `/pause`: Pause the current song

- `/resume`: Resume the current song

- `/stop`: Stop the current song

- `/leave`: Leave the voice channel

- `/volume`: Set the volume of the bot (0-100)

- `/stats`: Display bot statistics (uptime, servers, cpu usage, ...)
  
## Docker Images

Pre-built Docker images are automatically published to GitHub Container Registry on every commit:
- `ghcr.io/xerosic/symphony:latest` - Latest stable version
- `ghcr.io/xerosic/symphony:main` - Latest main branch

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
