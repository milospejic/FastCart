import os
from pydantic_settings import BaseSettings, SettingsConfigDict

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) 
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR)) 
ENV_PATH = os.path.join(ROOT_DIR, ".env")

class Settings(BaseSettings):
    product_database_url: str
    rabbitmq_url: str 
    
    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")

settings = Settings()