"""Project settings, read from environment variables or a .env file."""

from pathlib import Path

from pydantic import Field, SecretStr
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
    # CLIP model for the photo check: an open_clip model name and its weights. OpenAI's
    # weights need the -quickgelu variant, the activation they were trained with.
    clip_model: str = "ViT-B-32-quickgelu"
    clip_pretrained: str = "openai"

    # Vision language model for photos CLIP can't settle. For Azure OpenAI, the URL
    # is https://<resource>.openai.azure.com/openai/v1/ and the model is the
    # deployment name; for the OpenAI API, leave the URL empty.
    vision_base_url: str | None = None
    vision_api_key: SecretStr | None = None
    vision_model: str = "gpt-5-mini"

    # The agent's language models, on the same endpoint and key as the vision model.
    # The large model takes over a report the small one can't get past the self-check;
    # without one, the small model does everything.
    agent_model: str = "gpt-5-mini"
    agent_model_large: str | None = None

    # The schema that holds the audit log and the checkpoints of audits waiting for review
    review_schema: str = Field(default="review", pattern=r"^[a-z_][a-z0-9_]*$")
    # The review API, where the review screen finds it
    api_url: str = "http://127.0.0.1:8000"

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
