from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.db import init_db
from app.routes import router
from app.services.delivery_worker import DeliveryWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker = DeliveryWorker(settings=get_settings())
    app.state.delivery_worker = worker
    await worker.start()
    yield
    await worker.stop()


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router)
