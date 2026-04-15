"""
main.py — entry point for the Caldwell simulation server.

Start with:  python main.py
UI at:       http://localhost:8080
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database.db import init_db, SessionLocal
from simulation.engine import SimulationEngine
from simulation.clock import SimulationClock
from api.routes import router
from api.websocket_manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger("caldwell")

scheduler = AsyncIOScheduler()


async def scheduled_tick():
    """Called by APScheduler every TICK_INTERVAL_MINUTES."""
    db = SessionLocal()
    try:
        clock = SimulationClock(db)
        if not clock.is_running():
            return

        engine = SimulationEngine(db)

        async def broadcast(payload):
            await manager.broadcast({"type": "tick", "data": payload})

        result = await engine.run_tick(broadcast_fn=broadcast)
        logger.info(
            f"Auto-tick complete: {result.get('date_display')} | "
            f"Cost today: ${result.get('cost_today', 0):.4f}"
        )
    except Exception as e:
        logger.error(f"Scheduled tick error: {e}", exc_info=True)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Caldwell starting up...")
    init_db()

    scheduler.add_job(
        scheduled_tick,
        "interval",
        minutes=settings.tick_interval_minutes,
        id="main_tick",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — tick every {settings.tick_interval_minutes} minutes."
    )

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Caldwell shut down.")


app = FastAPI(
    title="Caldwell Simulation",
    description="Autonomous multi-agent sociological simulation",
    version="1.0.0",
    lifespan=lifespan,
)

# API routes
app.include_router(router)

# Serve static files (the UI)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/play")
async def serve_game():
    return FileResponse(os.path.join(static_dir, "game.html"))


@app.get("/analytics")
async def serve_dashboard():
    return FileResponse(os.path.join(static_dir, "dashboard.html"))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )
