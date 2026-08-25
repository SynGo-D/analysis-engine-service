import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .application.orchestrator import AnalysisOrchestrator
from .config import settings
from .consumers.pr_queue_consumer import PRQueueConsumer
from .infrastructure.database import connect_database
from .infrastructure.rabbitmq import connect_rabbitmq, create_channel
from .infrastructure.schema import ensure_schema
from .repositories.analysis_result_repository import AnalysisResultRepository
from .api.health import router as health_router
from .api.analysis import router as analysis_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown lifecycle. Shared resources (DB pool, RabbitMQ
    connection/channel) live on app.state — the idiomatic FastAPI pattern
    for dependency composition, replacing the older @app.on_event hooks.
    """
    app.state.db_pool = await connect_database()
    await ensure_schema(app.state.db_pool)

    app.state.analysis_result_repository = AnalysisResultRepository(app.state.db_pool)

    app.state.rabbitmq_connection = await connect_rabbitmq()
    app.state.rabbitmq_channel = await create_channel(app.state.rabbitmq_connection)

    orchestrator = AnalysisOrchestrator()
    consumer = PRQueueConsumer(orchestrator, app.state.analysis_result_repository)
    await consumer.start(app.state.rabbitmq_channel)

    yield

    # Graceful shutdown: close in reverse-acquisition order.
    await app.state.rabbitmq_connection.close()
    await app.state.db_pool.close()


app = FastAPI(title="Analysis Engine", lifespan=lifespan)

# Findings are read directly by the browser (web-interface calls this
# service the same way it calls integration-service — see lib/api.ts),
# so this needs real CORS, not just server-to-server access.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(analysis_router)
