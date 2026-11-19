import os
import time
from aiohttp import web
from config import PORT, OWNER_USERNAME, OWNER_CHANNEL_LINK, OWNER_GROUP_LINK, OWNER_INSTAGRAM_LINK, logger
from database import db

START_TIME = time.time()


async def health_check_handler(request):
    """Health check endpoint for Koyeb, Render, Railway, and uptime monitors."""
    uptime_seconds = int(time.time() - START_TIME)
    stats = db.get_stats()
    return web.json_response({
        "status": "healthy",
        "service": "String Gen Media Downloader Bot",
        "owner": f"@{OWNER_USERNAME}",
        "uptime_seconds": uptime_seconds,
        "total_users": stats["total_users"],
        "total_downloads": stats["total_downloads"],
    })


async def root_handler(request):
    """Web Dashboard showing Bot live status, analytics & social links."""
    uptime_seconds = int(time.time() - START_TIME)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    secs = uptime_seconds % 60
    stats = db.get_stats()

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>⚡ String Gen Media Bot - Live Status</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: linear-gradient(135deg, #0b0f19 0%, #1a1f35 100%);
                color: #f8fafc;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 1.5rem;
            }}
            .card {{
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 1.5rem;
                padding: 2.5rem;
                max-width: 520px;
                width: 100%;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
                text-align: center;
            }}
            .badge {{
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                background: rgba(34, 197, 94, 0.15);
                color: #4ade80;
                border: 1px solid rgba(34, 197, 94, 0.3);
                padding: 0.4rem 1rem;
                border-radius: 9999px;
                font-weight: 600;
                font-size: 0.85rem;
                margin-bottom: 1.5rem;
            }}
            .pulse {{
                width: 8px;
                height: 8px;
                background: #22c55e;
                border-radius: 50%;
                box-shadow: 0 0 10px #22c55e;
            }}
            h1 {{
                font-size: 1.75rem;
                font-weight: 800;
                background: linear-gradient(90deg, #38bdf8, #818cf8);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 0.5rem;
            }}
            p.subtitle {{
                color: #94a3b8;
                font-size: 0.95rem;
                margin-bottom: 2rem;
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 1rem;
                margin-bottom: 2rem;
            }}
            .stat-box {{
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 1rem;
                padding: 1rem;
            }}
            .stat-val {{
                font-size: 1.4rem;
                font-weight: 700;
                color: #38bdf8;
            }}
            .stat-lbl {{
                font-size: 0.75rem;
                color: #64748b;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                margin-top: 0.25rem;
            }}
            .btn-group {{
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }}
            .btn {{
                display: block;
                padding: 0.75rem 1.25rem;
                border-radius: 0.75rem;
                font-weight: 600;
                text-decoration: none;
                transition: transform 0.15s ease, background 0.15s ease;
                font-size: 0.9rem;
            }}
            .btn-primary {{
                background: #38bdf8;
                color: #0b0f19;
            }}
            .btn-primary:hover {{
                background: #7dd3fc;
                transform: translateY(-2px);
            }}
            .btn-secondary {{
                background: rgba(255, 255, 255, 0.05);
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
            .btn-secondary:hover {{
                background: rgba(255, 255, 255, 0.1);
                transform: translateY(-2px);
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="badge">
                <span class="pulse"></span>
                <span>24/7 LIVE & OPERATIONAL</span>
            </div>
            <h1>𝗦ᴛʀɪɴɢ 𝐆ᴇɴ 𝐁ᴏᴛ</h1>
            <p class="subtitle">World's #1 Multi-Platform Media & Audio Downloader Engine</p>

            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-val">{stats['total_users']}</div>
                    <div class="stat-lbl">Total Users</div>
                </div>
                <div class="stat-box">
                    <div class="stat-val">{stats['total_downloads']}</div>
                    <div class="stat-lbl">Downloads</div>
                </div>
                <div class="stat-box">
                    <div class="stat-val">{stats['today_downloads']}</div>
                    <div class="stat-lbl">Today's Tasks</div>
                </div>
                <div class="stat-box">
                    <div class="stat-val">{hours}h {minutes}m</div>
                    <div class="stat-lbl">Uptime</div>
                </div>
            </div>

            <div class="btn-group">
                <a class="btn btn-primary" href="https://t.me/String_rebot" target="_blank">🚀 Open Bot on Telegram</a>
                <a class="btn btn-secondary" href="{OWNER_CHANNEL_LINK}" target="_blank">📢 Updates Channel (@privatethink)</a>
                <a class="btn btn-secondary" href="{OWNER_GROUP_LINK}" target="_blank">💬 Support Group (@amrit_feel)</a>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")


async def api_test_handler(request):
    """Test endpoint to verify extraction on Koyeb live across all platforms."""
    url = request.query.get("url", "")
    if not url:
        return web.json_response({"error": "Missing url parameter"}, status=400)

    from downloader import MediaDownloader, sanitize_url, extract_snapsave_media
    downloader = MediaDownloader()
    clean_url = sanitize_url(url)
    result = {"url": url, "clean_url": clean_url}

    # Test SnapSave if Instagram or Facebook
    if "instagram.com" in clean_url or "facebook.com" in clean_url:
        try:
            import urllib.request, urllib.parse, re
            from downloader import _decode_snapsave
            data = urllib.parse.urlencode({'url': clean_url}).encode('utf-8')
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Origin': 'https://snapsave.app',
                'Referer': 'https://snapsave.app/'
            }
            req = urllib.request.Request('https://snapsave.app/action.php', data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')
            m = re.search(r'\(\"([^\"]+)\",\s*(\d+),\s*\"([^\"]+)\",\s*(\d+),\s*(\d+),\s*(\d+)\)', html_text)
            if m:
                h, u, n, t, e, _ = m.groups()
                decoded = _decode_snapsave(h, int(u), n, int(t), int(e))
                rapid = re.findall(r'https://d\.rapidcdn\.app/v2\?token=[^\s\"\'<>]+', decoded)
                result["snapsave"] = {
                    "matched": True,
                    "rapid_found": len(rapid) > 0,
                    "has_error": "Error:" in decoded,
                    "preview": decoded[:200]
                }
            else:
                result["snapsave"] = {"matched": False, "preview": html_text[:200]}
        except Exception as exc:
            result["snapsave"] = {"error": str(exc)}

    # Test fetch_media_info
    try:
        info = await downloader.fetch_media_info(clean_url)
        result["fetch"] = {
            "status": "success",
            "title": info.get("title"),
            "duration": info.get("duration"),
            "extractor": info.get("extractor"),
            "qualities": info.get("qualities"),
            "is_image": info.get("is_image"),
            "direct_download_url": bool(info.get("direct_download_url"))
        }
    except Exception as exc:
        result["fetch"] = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error_message": str(exc)
        }

    return web.json_response(result)


async def start_web_server():
    """Starts the background aiohttp web server on the assigned port."""
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_check_handler)
    app.router.add_get("/api/test", api_test_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health-check web server started on port {PORT} (0.0.0.0:{PORT})")
    return runner
