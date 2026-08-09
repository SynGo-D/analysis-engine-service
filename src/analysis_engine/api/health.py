from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health():
    """
    Liveness only — is the process up. No dependency checks, mirrors
    webhook-listener's /health: a slow/degraded database or broker
    shouldn't cause a healthy process to be killed and restarted for no
    reason.
    """
    return {"service": "analysis-engine", "status": "healthy"}


@router.get("/ready")
async def ready(request: Request):
    """
    Readiness — DB + RabbitMQ reachable. Checked by the load balancer /
    Kubernetes readiness probe before routing work to this instance.
    """
    db_ok = False
    try:
        async with request.app.state.db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1;")
        db_ok = True
    except Exception:
        db_ok = False

    rabbitmq_ok = not request.app.state.rabbitmq_connection.is_closed

    is_ready = db_ok and rabbitmq_ok

    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "service": "analysis-engine",
            "ready": is_ready,
            "checks": {"database": db_ok, "rabbitmq": rabbitmq_ok},
        },
    )
