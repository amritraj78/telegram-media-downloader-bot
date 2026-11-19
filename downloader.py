import os
import re
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import yt_dlp
import aiohttp
from config import TEMP_DOWNLOAD_DIR, COOKIES_FILE, MAX_FILE_SIZE_MB, logger

URL_PATTERN = re.compile(
    r'(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'https?://[a-zA-Z0-9]+\.[^\s]{2,})',
    re.IGNORECASE
)

INSTAGRAM_SHORTCODE_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)',
    re.IGNORECASE
)


def extract_urls(text: str) -> List[str]:
    """Extract all URLs from a given message text."""
    if not text:
        return []
    return URL_PATTERN.findall(text)


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


class MediaDownloader:
    def __init__(self):
        self.download_semaphore = asyncio.Semaphore(4)

    def _get_base_ydl_opts(self) -> Dict[str, Any]:
        """Ultra-fast, robust options for yt-dlp with bot-detection bypass."""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 10,  # Fast 10s timeout to prevent freezing
            'retries': 2,
            # Bypass YouTube bot challenge with mobile player client
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios'],
                    'player_skip': ['webpage', 'configs', 'js']
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
        if COOKIES_FILE and os.path.exists(COOKIES_FILE):
            opts['cookiefile'] = COOKIES_FILE
        return opts

    async def _extract_instagram_fallback(self, url: str) -> Optional[Dict[str, Any]]:
        """Fallback extractor using parth-dl or direct public GraphQL for Instagram reels."""
        shortcode_match = INSTAGRAM_SHORTCODE_PATTERN.search(url)
        if not shortcode_match:
            return None

        shortcode = shortcode_match.group(1)
        logger.info(f"Attempting Instagram fallback extraction for shortcode: {shortcode}")

        def _run_parth():
            try:
                from parth_dl.extractors import MediaExtractor
                extractor = MediaExtractor()
                return extractor.extract(url)
            except Exception as e:
                logger.warning(f"parth-dl extraction failed: {e}")
                return None

        result = await asyncio.to_thread(_run_parth)
        if result and result.get("entries"):
            entry = result["entries"][0]
            direct_video_url = entry.get("url")
            thumbnail = entry.get("thumbnail")
            title = entry.get("title") or f"Instagram Reel ({shortcode})"
            uploader = entry.get("uploader") or "Instagram Creator"

            return {
                "url": url,
                "direct_download_url": direct_video_url,
                "title": title[:60],
                "duration": entry.get("duration", 0),
                "duration_str": format_duration(entry.get("duration")),
                "thumbnail": thumbnail,
                "uploader": uploader,
                "extractor": "Instagram",
                "is_image": not bool(entry.get("is_video", True)),
                "image_url": entry.get("url") if not entry.get("is_video", True) else None,
                "qualities": ["best"],
                "raw_info": entry
            }

        return None

    async def fetch_media_info(self, url: str) -> Dict[str, Any]:
        """
        Extract media information without downloading the full media file.
        Uses primary yt-dlp engine with automated Instagram fallback.
        """
        # Quick check for Instagram
        is_instagram = "instagram.com" in url.lower() or "instagr.am" in url.lower()

        # Try primary yt-dlp extraction
        def _extract():
            opts = self._get_base_ydl_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = None
        try:
            info = await asyncio.to_thread(_extract)
        except Exception as e:
            logger.warning(f"Primary yt-dlp extraction failed for {url}: {e}")
            # If Instagram, try dedicated Instagram fallback
            if is_instagram:
                fallback_info = await self._extract_instagram_fallback(url)
                if fallback_info:
                    return fallback_info
            raise e

        if not info:
            if is_instagram:
                fallback_info = await self._extract_instagram_fallback(url)
                if fallback_info:
                    return fallback_info
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
            "url": url,
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
        If direct_download_url is provided (from Instagram fallback), downloads stream directly.
        """
        file_id = uuid.uuid4().hex[:10]
        output_template = str(TEMP_DOWNLOAD_DIR / f"{file_id}_%(title).50s.%(ext)s")

        # Fast direct download path if direct CDN link is provided
        if direct_download_url and media_type == "video":
            dest_file = str(TEMP_DOWNLOAD_DIR / f"{file_id}_video.mp4")
            async with aiohttp.ClientSession() as session:
                async with session.get(direct_download_url, timeout=60) as resp:
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
                            "title": "Instagram Video",
                            "duration": 0,
                            "uploader": "Instagram",
                            "thumbnail": None
                        }

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
                        'player_client': ['android', 'ios'],
                    }
                },
                'http_headers': {
                    'User-Agent': (
                        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) '
                        'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
                    ),
                }
            }

            if COOKIES_FILE and os.path.exists(COOKIES_FILE):
                ydl_opts['cookiefile'] = COOKIES_FILE

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
                info = ydl.extract_info(url, download=True)
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
