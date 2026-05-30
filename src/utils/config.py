# src/utils/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from functools import lru_cache

class Settings(BaseSettings):
    # === Base de Datos ===
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "agri_db"
    db_user: str = "agri_user"
    db_password: str = ""
    
    # === Ollama ===
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    generation_model: str = "qwen2.5:7b"
    
    # === Telegram & Seguridad ===
    telegram_token: str = ""
    ingesta_secret_key: str = ""

    # === GEE ===
    gee_project_id: str = ""
    
    # === Configuración Pydantic V2 ===
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / "config" / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,      # ✅ Permite DB_HOST -> db_host
        extra="ignore",            # ✅ Ignora variables no definidas en la clase
        validate_assignment=True   # ✅ Valida al asignar nuevos valores
    )
    
    @property
    def database_url(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

@lru_cache()
def get_settings() -> Settings:
    return Settings()

# Instancia global para importar fácil en todo el proyecto
settings = get_settings()