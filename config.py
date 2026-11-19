import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Credentials
BOT_TOKEN = os.getenv("BOT_TOKEN", "9232959902:AAHPnn41tLcR7_Oxz_Q5aSGwdT6HCRRD6W8").strip()

# Owner & Social Details
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ex_amrit").replace("@", "")
OWNER_CHANNEL_LINK = os.getenv("OWNER_CHANNEL_LINK", "https://t.me/privatethink")
OWNER_GROUP_LINK = os.getenv("OWNER_GROUP_LINK", "https://t.me/amrit_feel")
OWNER_INSTAGRAM_LINK = os.getenv("OWNER_INSTAGRAM_LINK", "https://instagram.com/ex_amrit")
OWNER_SNAPCHAT_ID = os.getenv("OWNER_SNAPCHAT_ID", "ex_amrit")
OWNER_SNAPCHAT_LINK = f"https://snapchat.com/add/{OWNER_SNAPCHAT_ID}"

# Force Subscribe Settings (Optional: Set True if bot is an admin in the channel/group)
ENABLE_FSUB = os.getenv("ENABLE_FSUB", "false").lower() in ("true", "1", "yes")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "privatethink").replace("@", "")
REQUIRED_GROUP = os.getenv("REQUIRED_GROUP", "amrit_feel").replace("@", "")

# Server & Cloud Hosting
PORT = int(os.getenv("PORT", "8000"))

# Concurrency & Performance
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))  # 50MB Telegram Bot API limit

# File Paths
BASE_DIR = Path(__file__).resolve().parent
TEMP_DOWNLOAD_DIR = BASE_DIR / "downloads"
TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_FILE = os.getenv("COOKIES_FILE", "")
if COOKIES_FILE and not os.path.exists(COOKIES_FILE):
    COOKIES_FILE = ""

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger("StringGenMediaBot")
