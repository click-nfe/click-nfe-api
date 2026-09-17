import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()

DEFAULT_NFE_XSD_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "nfe_schemas"
    / "PL_010e_v1.02"
    / "nfe_v4.00.xsd"
)


def _database_uri():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    return URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("DB_USER", "click_nfe"),
        password=os.getenv("DB_PASSWORD", "click_nfe"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "click_nfe"),
    )


def _csv_env(name: str, default: str) -> list[str]:
    raw_value = os.getenv(name, default)
    return [
        value.strip()
        for value in raw_value.split(",")
        if value.strip()
    ]


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SECRET_KEY = os.getenv("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    CORS_ORIGINS = _csv_env(
        "CORS_ORIGINS",
        "http://localhost:3000",
    )
    CORS_SUPPORTS_CREDENTIALS = True

    JWT_ACCESS_EXPIRES_SECONDS = int(
        os.getenv("JWT_ACCESS_EXPIRES_SECONDS", "3600")
    )
    JWT_REFRESH_EXPIRES_SECONDS = int(
        os.getenv("JWT_REFRESH_EXPIRES_SECONDS", "604800")
    )
    BRASIL_API_BASE_URL = os.getenv(
        "BRASIL_API_BASE_URL",
        "https://brasilapi.com.br/api",
    )
    BRASIL_API_TIMEOUT_SECONDS = float(
        os.getenv("BRASIL_API_TIMEOUT_SECONDS", "8")
    )
    NFE_XSD_PATH = os.getenv(
        "NFE_XSD_PATH",
        str(DEFAULT_NFE_XSD_PATH),
    )
