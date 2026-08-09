import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .application.orchestrator import AnalysisOrchestrator
from .consumers.pr_queue_consumer import PRQueueConsumer
from .infrastructure.database import connect_database
from .infrastructure.rabbitmq import connect_rabbitmq, create_channel
from .api.health import router as health_router

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
    app.state.rabbitmq_connection = await connect_rabbitmq()
    app.state.rabbitmq_channel = await create_channel(app.state.rabbitmq_connection)

    orchestrator = AnalysisOrchestrator()
    consumer = PRQueueConsumer(orchestrator)
    await consumer.start(app.state.rabbitmq_channel)

    yield

    # Graceful shutdown: close in reverse-acquisition order.
    await app.state.rabbitmq_connection.close()
    await app.state.db_pool.close()


app = FastAPI(title="Analysis Engine", lifespan=lifespan)

app.include_router(health_router)
