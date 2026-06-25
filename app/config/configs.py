from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Configs(BaseSettings):
    DB_PATH: str = str(Path(__file__).resolve().parent.parent.parent / "jobs.db")
    DB_TIMEOUT: int = 5
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


configs = Configs()
