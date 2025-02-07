# 🎼 Symphony - Simple self-hostable Discord musicbot 

Symphony is a simple self-hostable Discord musicbot that can play music from YouTube.

## Installation

1. Clone the repository
2. Rename the `.env.example` file to `.env`

```bash
cp .env.example .env
```

3. Modify the `.env` file with your Discord bot token
4. Build the Docker image

```bash
docker build -t symphony-bot .
```

5. Run the Docker container

```bash
docker run -d --env-file .env symphony-bot
```

## Usage

_The bot supports slash commands_

- `!play <query>`: Play a song from YouTube (both URL and query are supported)
- `!skip`: Skip the current song
- `!pause`: Pause the current song
- `!resume`: Resume the current song
- `!stop`: Stop the current song
- `!leave`: Leave the voice channel

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
