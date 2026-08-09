from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralised, type-safe access to all environment variables — mirrors
    the Node services' src/config/env.ts pattern so the codebases stay
    consistent to read. Fields with no default are required: pydantic-
    settings raises a ValidationError at import time if they're missing,
    which is this service's fail-fast equivalent of the Node services'
    `process.env.X!` non-null assertions.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    port: int = 8000

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    rabbitmq_url: str = "amqp://guest:guest@localhost:5672"

    # How many unacked jobs this worker process will hold at once. 1 by
    # default: analysis jobs are long-running (repo clone + linters), so
    # this worker should finish (ack/nack) one before RabbitMQ hands it
    # another — scale by running more worker processes, not by widening
    # this.
    consumer_prefetch_count: int = 1


settings = Settings()
