from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI-compatible (Qwen)
    OPENAI_API_KEY: str  = "your-dashscope-key"
    OPENAI_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    MODEL_NAME: str      = "qwen-plus-latest"   # qwen3.6-plus API model string

    # MongoDB
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str  = "course_gen"

    # Redis (arq broker + result backend)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Generation
    MAX_REVIEW_RETRIES: int = 2   # reduced for cost; description anchoring covers quality

    # ── Concurrency / throughput (NEW) ────────────────────────────────────────
    # How many jobs a single arq worker processes in parallel.
    # Raise this to scale up; you can also start multiple worker processes.
    WORKER_MAX_JOBS: int = 30

    # Hard cap on simultaneous LLM API calls in this process. Acts as a circuit
    # breaker against the upstream provider's rate limits — even if 30 worker
    # jobs are running, at most this many will be hitting the LLM at once.
    # Tune to your provider's RPM quota: LLM_MAX_CONCURRENCY * (avg_call_seconds)
    # should be < RPM_quota / 60.
    LLM_MAX_CONCURRENCY: int = 20

    # Retry policy for transient LLM errors (429 / 5xx / timeout).
    LLM_MAX_RETRIES: int    = 3
    LLM_RETRY_BACKOFF: float = 2.0   # base seconds; exponential: 2, 4, 8 ...
    LLM_TIMEOUT: float      = 180.0  # per-request HTTP timeout

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
