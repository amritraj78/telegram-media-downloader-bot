import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logging.warning("BOT_TOKEN is not set in environment or .env file!")

# Health check web server port (Crucial for Koyeb/Render/Railway)
PORT = int(os.getenv("PORT", "8000"))

# Concurrency & Size limits
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))  # 50MB is Telegram Bot API upload limit

# Storage directories
BASE_DIR = Path(__file__).resolve().parent
TEMP_DOWNLOAD_DIR = BASE_DIR / "downloads"
TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Optional cookies file for age-restricted/restricted platforms
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
if COOKIES_FILE and not os.path.exists(COOKIES_FILE):
    logging.warning(f"Specified COOKIES_FILE '{COOKIES_FILE}' was not found. Continuing without cookies.")
    COOKIES_FILE = ""

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger("MediaDownloaderBot")
