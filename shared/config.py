from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB settings
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "petrosa"

    # NATS settings
    nats_servers: str = "nats://localhost:4222"
    nats_signal_subject: str = "signals.trading"

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
