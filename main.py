from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.main_router import router
from config import settings

log = logging.getLogger("course-gen.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Banner — pairs with the one in tasks/worker.py:on_startup. If the
    # MONGO_DB/MONGO_URI printed here doesn't match the worker's banner,
    # generation tasks will look up data in a different DB than the API
    # wrote to, and you'll see "X not found" errors in the worker.
    log.info(
        "course-gen API booting | MONGO_URI=%s MONGO_DB=%s REDIS_URL=%s",
        settings.MONGO_URI, settings.MONGO_DB, settings.REDIS_URL,
    )

    # Build indexes once on API server startup. (The arq worker also does this
    # — both are idempotent.) Without indexes, the hot read paths used by every
    # generation task fall back to collection scans.
    from models import ensure_indexes
    await ensure_indexes()
    yield


app = FastAPI(title="Course Generator", version="2.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    # Ensure log lines from our banner show up under the default uvicorn level.
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
