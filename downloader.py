import os
import re
import uuid
import asyncio
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import yt_dlp
import aiohttp
from config import TEMP_DOWNLOAD_DIR, BASE_DIR, MAX_FILE_SIZE_MB, logger

COOKIES_PATH = BASE_DIR / "cookies.txt"

URL_PATTERN = re.compile(
    r'(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'https?://[a-zA-Z0-9]+\.[^\s]{2,})',
    re.IGNORECASE
)

INSTAGRAM_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)',
    re.IGNORECASE
)

FACEBOOK_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?(?:facebook\.com|fb\.watch)/',
    re.IGNORECASE
)

YOUTUBE_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]+)',
    re.IGNORECASE
)


def extract_urls(text: str) -> List[str]:
    """Extract all URLs from a given message text."""
    if not text:
        return []
    return URL_PATTERN.findall(text)


def sanitize_url(url: str) -> str:
    """Strips tracking query parameters from Instagram and YouTube URLs."""
    # Instagram clean canonical URL
    m_ig = re.search(r'(https?://(?:www\.)?instagram\.com/(?:reel|p|tv)/[A-Za-z0-9_-]+)', url)
    if m_ig:
        return m_ig.group(1) + '/'

    # YouTube Shorts clean canonical URL
    m_yt_short = re.search(r'(https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]+)', url)
    if m_yt_short:
        return m_yt_short.group(1)

    # YouTube Watch clean URL
    m_yt_watch = re.search(r'(https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]+)', url)
    if m_yt_watch:
        return m_yt_watch.group(1)

    # youtu.be clean URL
    m_yt_be = re.search(r'(https?://youtu\.be/[A-Za-z0-9_-]+)', url)
    if m_yt_be:
        return m_yt_be.group(1)

    return url


def format_duration(seconds: Optional[int]) -> str:
    """Format duration in seconds to MM:SS or HH:MM:SS."""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_bytes(num_bytes: Optional[int]) -> str:
    """Format bytes to human readable string (KB, MB, GB)."""
    if not num_bytes:
        return ""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def _decode_snapsave(h: str, u: int, n: str, t: int, e: int) -> str:
    """Pure Python implementation of SnapSave JS de-obfuscation unpacker."""
    charset = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/'

    def _e23c(d_val, e_val, f_val):
        h_chars = charset[:e_val]
        i_chars = charset[:f_val]
        j_val = 0
        for c, b in enumerate(reversed(d_val)):
            idx = h_chars.find(b)
            if idx != -1:
                j_val += idx * (e_val ** c)
        k_str = ''
        while j_val > 0:
            k_str = i_chars[j_val % f_val] + k_str
            j_val //= f_val
        return k_str or '0'

    res = []
    i = 0
    delimiter = n[e]
    while i < len(h):
        s = ''
        while i < len(h) and h[i] != delimiter:
            s += h[i]
            i += 1
        i += 1
        for j in range(len(n)):
            s = s.replace(n[j], str(j))
        if s:
            char_code = int(_e23c(s, e, 10)) - t
            res.append(chr(char_code))
    return ''.join(res)


def extract_snapsave_media(url: str) -> Optional[Dict[str, Any]]:
    """
    Extracts direct CDN media URLs via SnapSave engine with sanitized URLs.
    Bypasses Instagram & Facebook datacenter IP blocks completely.
    """
    clean_url = sanitize_url(url)
    try:
        data = urllib.parse.urlencode({'url': clean_url}).encode('utf-8')
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
            'Origin': 'https://snapsave.app',
            'Referer': 'https://snapsave.app/'
        }
        req = urllib.request.Request('https://snapsave.app/action.php', data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html_text = resp.read().decode('utf-8', errors='ignore')

        match = re.search(r'\(\"([^\"]+)\",\s*(\d+),\s*\"([^\"]+)\",\s*(\d+),\s*(\d+),\s*(\d+)\)', html_text)
        if not match:
            return None

        h, u, n, t, e, _ = match.groups()
        decoded = _decode_snapsave(h, int(u), n, int(t), int(e))

        if "Error:" in decoded:
            logger.warning(f"SnapSave returned error for {clean_url}")
            return None

        rapid_urls = re.findall(r'https://d\.rapidcdn\.app/v2\?token=[^\s\"\'<>]+', decoded)
        thumb_urls = re.findall(r'https://d\.rapidcdn\.app/thumb\?token=[^\s\"\'<>]+', decoded)

        if not rapid_urls:
            rapid_urls = re.findall(r'href=\\?\"(https?://[^\s\"\'<>]+)\\?\"', decoded)

        if not rapid_urls:
            return None

        video_url = rapid_urls[0].replace('\\\\', '').rstrip('\\\"')
        thumb_url = thumb_urls[0].replace('\\\\', '').rstrip('\\\"') if thumb_urls else None

        return {
            "url": clean_url,
            "direct_download_url": video_url,
            "title": "Social Video",
            "duration": 0,
            "duration_str": "HD",
            "thumbnail": thumb_url,
            "uploader": "Creator",
            "extractor": "Instagram/FB",
            "is_image": False,
            "image_url": None,
            "qualities": ["best"],
            "raw_info": {}
        }
    except Exception as e:
        logger.warning(f"SnapSave extraction exception: {e}")
        return None


class MediaDownloader:
    def __init__(self):
        self.download_semaphore = asyncio.Semaphore(4)

    def _get_base_ydl_opts(self) -> Dict[str, Any]:
        """Options for yt-dlp with TV embedded & mweb player clients for YouTube."""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 15,
            'retries': 2,
            # YouTube: Use tv_embedded & mweb to avoid PO Token requirement
            'extractor_args': {
                'youtube': {
                    'player_client': ['tv_embedded', 'mweb', 'android', 'ios'],
                },
                'instagram': {
                    'include_logged_out': True,
                }
            },
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) '
                    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
                ),
                'Accept-Language': 'en-US,en;q=0.9',
            }
        }
        # Dynamic cookie detection
        if os.path.exists(COOKIES_PATH):
            opts['cookiefile'] = str(COOKIES_PATH)
        return opts

    async def fetch_media_info(self, url: str) -> Dict[str, Any]:
        """
        Extract media information without downloading the full media file.
        Uses SnapSave first for Instagram and Facebook with sanitized URL.
        """
        clean_url = sanitize_url(url)
        is_instagram = bool(INSTAGRAM_PATTERN.search(clean_url))
        is_facebook = bool(FACEBOOK_PATTERN.search(clean_url))

        # Instagram & Facebook: SnapSave first (bypasses datacenter block)
        if is_instagram or is_facebook:
            logger.info(f"Running high-speed SnapSave engine for: {clean_url}")
            snapsave_result = await asyncio.to_thread(extract_snapsave_media, clean_url)
            if snapsave_result and snapsave_result.get("direct_download_url"):
                logger.info("SnapSave successfully extracted direct media URL!")
                return snapsave_result

        # Primary yt-dlp extraction with clean URL
        def _extract():
            opts = self._get_base_ydl_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(clean_url, download=False)

        info = None
        try:
            info = await asyncio.to_thread(_extract)
        except Exception as e:
            logger.warning(f"Primary yt-dlp extraction failed for {clean_url}: {e}")
            # Try SnapSave as last resort
            snapsave_result = await asyncio.to_thread(extract_snapsave_media, clean_url)
            if snapsave_result and snapsave_result.get("direct_download_url"):
                return snapsave_result
            raise e

        if not info:
            snapsave_result = await asyncio.to_thread(extract_snapsave_media, clean_url)
            if snapsave_result and snapsave_result.get("direct_download_url"):
                return snapsave_result
            raise ValueError("Could not extract any media information from this link.")

        if 'entries' in info and info['entries']:
            info = info['entries'][0]

        title = info.get('title') or "Media File"
        if len(title) > 60:
            title = title[:57] + "..."

        duration = info.get('duration')
        thumbnail = info.get('thumbnail')
        uploader = info.get('uploader') or info.get('channel') or "Unknown"
        extractor = info.get('extractor_key') or "Web"

        is_image = False
        image_url = None
        ext = info.get('ext', '')
        if ext in ['jpg', 'jpeg', 'png', 'webp']:
            is_image = True
            image_url = info.get('url') or thumbnail

        formats = info.get('formats') or []
        available_heights = set()
        max_height = 0

        for f in formats:
            h = f.get('height')
            if h and isinstance(h, int) and h > 0:
                available_heights.add(h)
                if h > max_height:
                    max_height = h

        standard_qualities = [1080, 720, 480, 360]
        offered_qualities = []

        if max_height > 0:
            for q in standard_qualities:
                if max_height >= q - 20:
                    offered_qualities.append(q)
            if not offered_qualities:
                offered_qualities.append(max_height)
        else:
            offered_qualities = ["best"]

        return {
            "url": clean_url,
            "title": title,
            "duration": duration,
            "duration_str": format_duration(duration),
            "thumbnail": thumbnail,
            "uploader": uploader,
            "extractor": extractor,
            "is_image": is_image,
            "image_url": image_url,
            "qualities": offered_qualities,
            "raw_info": info
        }

    async def download_media(
        self,
        url: str,
        media_type: str = "video",
        quality: Any = "best",
        direct_download_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Download media asynchronously.
        If direct_download_url is provided (from SnapSave), streams file directly with chunked transfer.
        """
        file_id = uuid.uuid4().hex[:10]
        output_template = str(TEMP_DOWNLOAD_DIR / f"{file_id}_%(title).50s.%(ext)s")
        clean_url = sanitize_url(url)

        # Fast direct download path if direct CDN link is provided
        if direct_download_url and media_type == "video":
            dest_file = str(TEMP_DOWNLOAD_DIR / f"{file_id}_video.mp4")
            async with aiohttp.ClientSession() as session:
                async with session.get(direct_download_url, timeout=60, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                    if resp.status == 200:
                        with open(dest_file, "wb") as f:
                            while True:
                                chunk = await resp.content.read(1024 * 64)
                                if not chunk:
                                    break
                                f.write(chunk)
                        file_size = os.path.getsize(dest_file)
                        return {
                            "file_path": dest_file,
                            "file_size": file_size,
                            "file_size_mb": file_size / (1024 * 1024),
                            "title": "Downloaded Video",
                            "duration": 0,
                            "uploader": "Social Media",
                            "thumbnail": None
                        }

        # yt-dlp download path
        def _download():
            ydl_opts = {
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'socket_timeout': 30,
                'retries': 3,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['tv_embedded', 'mweb', 'android', 'ios'],
                    }
                },
                'http_headers': {
                    'User-Agent': (
                        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) '
                        'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
                    ),
                }
            }

            if os.path.exists(COOKIES_PATH):
                ydl_opts['cookiefile'] = str(COOKIES_PATH)

            if media_type == "audio":
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                })
            else:
                if quality == "best":
                    ydl_opts['format'] = (
                        'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best'
                    )
                else:
                    height = int(quality)
                    ydl_opts['format'] = (
                        f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/'
                        f'bestvideo[height<={height}]+bestaudio/'
                        f'best[height<={height}][ext=mp4]/'
                        f'best[height<={height}]/best'
                    )
                ydl_opts['merge_output_format'] = 'mp4'

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=True)
                if 'entries' in info and info['entries']:
                    info = info['entries'][0]

                downloaded_file = ydl.prepare_filename(info)

                if media_type == "audio":
                    base, _ = os.path.splitext(downloaded_file)
                    downloaded_file = f"{base}.mp3"
                else:
                    base, _ = os.path.splitext(downloaded_file)
                    if os.path.exists(f"{base}.mp4"):
                        downloaded_file = f"{base}.mp4"

                return downloaded_file, info

        async with self.download_semaphore:
            file_path, info = await asyncio.to_thread(_download)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Target file was not found after download: {file_path}")

        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)

        return {
            "file_path": file_path,
            "file_size": file_size,
            "file_size_mb": file_size_mb,
            "title": info.get("title", "Media"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "thumbnail": info.get("thumbnail"),
        }

    async def download_direct_image(self, image_url: str) -> str:
        """Download a direct image URL (e.g. for Pinterest photos)."""
        file_id = uuid.uuid4().hex[:10]
        ext = "jpg"
        if ".png" in image_url.lower():
            ext = "png"
        elif ".webp" in image_url.lower():
            ext = "webp"

        dest_path = TEMP_DOWNLOAD_DIR / f"img_{file_id}.{ext}"

        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    return str(dest_path)
                else:
                    raise Exception(f"Failed to download image. HTTP {resp.status}")

    @staticmethod
    def cleanup_files(*file_paths: Optional[str]):
        """Clean up local temporary media files safely."""
        for path in file_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logger.debug(f"Deleted temp file: {path}")
                except Exception as e:
                    logger.warning(f"Could not remove file {path}: {e}")
