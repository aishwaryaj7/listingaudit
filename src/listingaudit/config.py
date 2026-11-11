"""Project settings, read from environment variables or a .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    postgres_user: str = "listingaudit"
    postgres_password: str = "listingaudit"
    postgres_db: str = "listingaudit"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Read-only role for the agent; it has no access to answer_key
    agent_db_user: str = "listingaudit_agent"
    agent_db_password: str = "listingaudit_agent"

    data_dir: Path = PROJECT_ROOT / "data"
    abo_base_url: str = "https://amazon-berkeley-objects.s3.amazonaws.com"
    # Hugging Face model used to embed the rulebook
    embedding_model: str = "intfloat/multilingual-e5-small"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def agent_database_url(self) -> str:
        return (
            f"postgresql://{self.agent_db_user}:{self.agent_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        # pandas uses SQLAlchemy, which needs the psycopg 3 driver in the URL
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def abo_raw_dir(self) -> Path:
        return self.data_dir / "raw" / "abo"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def laws_dir(self) -> Path:
        return self.data_dir / "laws"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


settings = Settings()
