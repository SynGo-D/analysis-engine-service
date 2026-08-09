import aio_pika
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection

from ..config import settings


async def connect_rabbitmq() -> AbstractRobustConnection:
    """
    Connects to the shared platform RabbitMQ broker (see docker-compose.yml
    and .env.example — this must be the SAME broker webhook-listener
    publishes to, not a separate instance).

    Uses `connect_robust`, which handles reconnection with backoff
    automatically. This is aio-pika's built-in equivalent of the manual
    reconnect-with-backoff logic webhook-listener implements by hand for
    amqplib (which has no built-in robust-connection mode) — no custom
    reconnection code is needed here as a result.

    Fail-fast on the *initial* connection, same as Postgres above.
    """
    try:
        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        print("Analysis Engine connected to RabbitMQ.")
        return connection

    except Exception as error:
        print(f"Failed to connect to RabbitMQ: {error}")
        raise


async def create_channel(connection: AbstractRobustConnection) -> AbstractRobustChannel:
    """
    Opens a channel on the given (robust) connection.

    Because the connection is robust, this channel automatically
    re-declares its queues/exchanges/consumers after a reconnect — aio-pika
    propagates "robustness" through connection -> channel -> queue ->
    consumer. Unlike webhook-listener's amqplib setup, which needed a
    manual onRabbitMQReconnect hook to re-run topology setup by hand after
    every reconnect, no equivalent hook is needed here.
    """
    return await connection.channel()
