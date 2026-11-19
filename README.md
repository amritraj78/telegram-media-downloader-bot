# 🚀 Universal Telegram Media & Audio Downloader Bot

A production-grade, highly efficient, 24/7 Telegram bot that downloads videos, reels, stories, shorts, posts, photos, and music/audio from almost any social platform with **interactive inline quality buttons** and **audio extraction (MP3)**.

---

## 🌟 Supported Platforms
- 📸 **Instagram**: Reels, Stories, Posts, IGTV, Audio
- 🎥 **YouTube**: Videos, Shorts (1080p, 720p, 480p, 360p, MP3)
- 📌 **Pinterest**: Video Pins, High-Res Photos/Images
- 📘 **Facebook**: Watch Videos, Reels, Stories
- 🐦 **Twitter / X**: Videos, Clips, Posts
- 🤖 **Reddit**: Videos with audio merged, GIFs
- 👻 **Snapchat**: Spotlight, Public Stories
- 🎵 **TikTok, Vimeo, SoundCloud, Twitch Clips & 1800+ more!**

---

## ⚡ Key Highlights
1. **Interactive Quality Selection Buttons**:
   - Detects all available resolutions for the video (1080p, 720p, 480p, 360p) and lets the user choose with one tap.
2. **Download All Sounds / Songs (MP3)**:
   - Dedicated `🎵 Download Audio / Song (MP3)` button for every video link to extract high-quality 192kbps MP3 audio with artist/title tags.
3. **24/7 Zero-Downtime Architecture**:
   - Includes an embedded `aiohttp` web server (on port `8000` or `$PORT`) responding to `/health` probes.
   - Compatible with **Koyeb**, **Render**, **Railway**, or Docker hosting.
   - Auto-reconnect on network drops, Telegram flood-wait handling, and automatic temporary file cleanup.
4. **Telegram File Limit Protection**:
   - Automatically prevents bot crashes when files exceed the Telegram Bot API 50 MB limit, offering clear user guidance.

---

## 🛠️ Project Structure

```
telegram-media-downloader-bot/
├── bot.py             # Main Telegram bot handlers & callback logic
├── downloader.py      # yt-dlp media extraction, formatting & FFmpeg merger
├── server.py          # Embedded health-check web server for 24/7 cloud uptime
├── config.py          # Environment configuration & path manager
├── requirements.txt   # Python dependencies
├── Dockerfile         # Production Docker container with FFmpeg
├── docker-compose.yml # 1-click Docker Compose config
├── Procfile           # PaaS process file (Koyeb/Render/Railway)
├── .env.example       # Template configuration
└── README.md          # Complete documentation
```

---

## 🚀 Setup & Deployment

### Step 1: Create Your Bot on Telegram
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the instructions to choose a name and username.
3. Copy the **HTTP API Bot Token** provided by BotFather.

---

### Step 2: Deploy to Koyeb (24/7 Free Cloud Hosting)

1. **Push this repository to GitHub**:
   - Create a new repository on your GitHub account (e.g. `telegram-downloader-bot`).
   - Push this codebase to your repository.
2. **Log into [Koyeb](https://www.koyeb.com)**:
   - Click **Create Service** -> **GitHub**.
   - Select your repository.
3. **Configure Service**:
   - **Service Type**: Web Service
   - **Builder**: `Dockerfile` (Koyeb will automatically detect the Dockerfile)
   - **Port**: `8000` (Protocol: HTTP, Path: `/health`)
4. **Add Environment Variables**:
   - `BOT_TOKEN`: Your Telegram Bot Token from `@BotFather`
   - `PORT`: `8000`
5. **Click Deploy**:
   - Koyeb will build the image with `ffmpeg` and start the bot.
   - The health check on `/health` will keep it running 24/7!

---

### Step 3: Run Locally or on a VPS (Linux / Docker)

#### Option A: Using Docker (Recommended)
```bash
# 1. Clone your repo
cd telegram-media-downloader-bot

# 2. Create your .env file
cp .env.example .env
nano .env  # Paste your BOT_TOKEN

# 3. Start the container in background
docker compose up -d --build

# View real-time logs:
docker compose logs -f
```

#### Option B: Direct Python Execution
```bash
# Ensure ffmpeg is installed on your system:
# Debian/Ubuntu: sudo apt install ffmpeg
# Termux: pkg install ffmpeg python

# Install dependencies:
pip install -r requirements.txt

# Create .env:
cp .env.example .env
# Edit .env and set your BOT_TOKEN

# Start the bot:
python bot.py
```

---

## 🛡️ Best Practices for 24/7 Stability

- **Keepalive Ping**: You can set up a free monitor at [UptimeRobot](https://uptimerobot.com) pointing to your Koyeb app URL (`https://<your-app>.koyeb.app/health`) to ping every 5 minutes and guarantee it never sleeps.
- **Cookies (Optional)**: If you want to download private Instagram stories or videos restricted by age, export a `cookies.txt` from your browser using an extension like *Get cookies.txt LOCALLY* and set `COOKIES_FILE=cookies.txt` in your `.env`.
