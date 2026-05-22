import os
from pydantic_settings import BaseSettings, SettingsConfigDict

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) 
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR)) 
ENV_PATH = os.path.join(ROOT_DIR, ".env")

class Settings(BaseSettings):
    review_database_url: str
    secret_key: str 
    algorithm: str = "HS256"
    frontend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")

settings = Settings()