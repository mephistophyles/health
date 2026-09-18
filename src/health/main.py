import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from health.api import charts, groceries, library, members, periods, plans, settings_api, tracking
from health.database import Database
from health.llm.jobs import worker_loop
from health.services import prune_llm_calls
from health.settings import get_settings


def configure_logging() -> None:
    """Structured lines to stdout. No log files, and never an LLM prompt or response."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter('{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}')
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


log = logging.getLogger('health')


async def _daily_prune(database: Database, stop: asyncio.Event) -> None:
    """Drop LLM call records past the retention window (PRD 4)."""
    settings = get_settings()
    while not stop.is_set():
        try:
            with database.worker_connection() as connection:
                removed = prune_llm_calls(
                    connection, database.household_id, settings.llm_log_retention_days
                )
            if removed:
                log.info('pruned %s llm_call rows', removed)
        except Exception:  # housekeeping must not take the app down
            log.exception('prune failed')
        try:
            await asyncio.wait_for(stop.wait(), timeout=timedelta(hours=6).total_seconds())
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    database.open()
    app.state.database = database
    app.state.stop = asyncio.Event()
    app.state.tasks = []
    if settings.job_worker_enabled:
        app.state.tasks.append(asyncio.create_task(worker_loop(database, settings, app.state.stop)))
        app.state.tasks.append(asyncio.create_task(_daily_prune(database, app.state.stop)))
    try:
        yield
    finally:
        app.state.stop.set()
        for task in app.state.tasks:
            task.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        database.close()


app = FastAPI(title='Health', lifespan=lifespan)

for router in (
    members.router,
    periods.router,
    plans.router,
    groceries.router,
    library.router,
    tracking.router,
    charts.router,
    settings_api.router,
):
    app.include_router(router)


@app.get('/healthz')
def healthz() -> dict[str, str]:
    """Green only when the app can actually serve: database reachable, assets mounted."""
    with app.state.database.pool.connection() as connection:
        connection.execute('SELECT 1')
    if not (WEB_DIST / 'index.html').exists():
        raise HTTPException(503, 'frontend assets missing')
    return {'status': 'ok', 'time': datetime.now(get_settings().tz).isoformat()}


@app.exception_handler(ValueError)
def value_error_handler(request: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={'detail': str(error)})


WEB_DIST = Path(os.environ.get('WEB_DIST_PATH', get_settings().web_dist_path))
if not WEB_DIST.exists():
    raise RuntimeError(
        f'WEB_DIST_PATH {WEB_DIST} does not exist; the frontend cannot be served. '
        'Set WEB_DIST_PATH to the built assets directory, or run `npm run build` first.'
    )
app.mount('/assets', StaticFiles(directory=WEB_DIST / 'assets'), name='assets')


@app.get('/{path:path}', include_in_schema=False)
def frontend(path: str) -> FileResponse:
    """The single-page app. An unknown /api path is a 404, not a page of HTML."""
    if path.startswith('api/'):
        raise HTTPException(404, 'No such endpoint')
    index = WEB_DIST / 'index.html'
    if not index.exists():
        raise HTTPException(404)
    return FileResponse(index)
