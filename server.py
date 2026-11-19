import os
import time
from aiohttp import web
from config import PORT, logger

START_TIME = time.time()

async def health_check_handler(request):
    """Health check endpoint for Koyeb, Render, Railway, and uptime monitors."""
    uptime_seconds = int(time.time() - START_TIME)
    return web.json_response({
        "status": "healthy",
        "service": "Telegram Media Downloader Bot",
        "uptime_seconds": uptime_seconds,
        "uptime_human": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"
    })

async def root_handler(request):
    """Root homepage showing bot status."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Media Downloader Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                   display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;
                   background: #0f172a; color: #f8fafc; }
            .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 450px; }
            h1 { color: #38bdf8; margin-bottom: 0.5rem; font-size: 1.6rem; }
            .badge { display: inline-block; background: #22c55e; color: #000; padding: 0.35rem 0.85rem; border-radius: 9999px; font-weight: bold; font-size: 0.9rem; margin-bottom: 1.5rem; }
            p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Telegram Downloader Bot</h1>
            <div class="badge">● 24/7 LIVE & OPERATIONAL</div>
            <p>The Telegram multi-platform media downloader bot is running and actively processing downloads.</p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

async def start_web_server():
    """Starts the background aiohttp web server on the assigned port."""
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_check_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health-check web server started on port {PORT} (0.0.0.0:{PORT})")
    return runner
