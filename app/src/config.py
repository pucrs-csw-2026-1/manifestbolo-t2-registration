from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


# defines the configuration for the application, each field contains the type and a default value that are overwritten by the environment variables (handled by pydantic).
# Meaning that, if you need to change the value of a field, change it in the .env file and keep this file as it is, unless the structure of the environment variables changes, in that case, you will need to change the structure of this file as well.
class Settings(BaseSettings):
    # Postgres pieces (some projects expose individual vars and not a full URL)
    POSTGRES_DB: str = "app_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432

    # Full URL may be provided directly; if not, we'll construct it from the pieces above
    DATABASE_URL: str | None = None

    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str = "http://localhost:4566"
    AWS_ACCESS_KEY_ID: str = "test"
    AWS_SECRET_ACCESS_KEY: str = "test"

    APP_NAME: str = "App"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    AUTH_SERVICE_BASE_URL: str = "http://localhost:8080"
    EVENTS_SERVICE_BASE_URL: str = "http://localhost:3000"

    # O events-service (avengers) exige Bearer em TODAS as rotas. O T2 se
    # autentica como serviço (OAuth2 client_credentials) no auth-service e envia
    # o token de máquina resultante. Em produção, configure um client dedicado;
    # os defaults abaixo batem com o client de dev do 0x_t1.
    EVENTS_SERVICE_CLIENT_ID: str = "metrics-service"
    EVENTS_SERVICE_CLIENT_SECRET: str = "dev-metrics-secret"
    EVENTS_SERVICE_CLIENT_SCOPE: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        if not self.DATABASE_URL:
            encoded_password = quote_plus(self.POSTGRES_PASSWORD)
            self.DATABASE_URL = (
                f"postgresql+psycopg2://{self.POSTGRES_USER}:{encoded_password}@"
                f"{self.DB_HOST}:{self.DB_PORT}/{self.POSTGRES_DB}"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
