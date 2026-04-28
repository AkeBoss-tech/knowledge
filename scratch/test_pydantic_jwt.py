from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import jwt

class TestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path("/Users/akashdubey/Documents/CodingProjects/RAIL/RutgersAgenticIntelligenceLabs/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    github_app_private_key: str = Field(default="", validation_alias="GITHUB_APP_PRIVATE_KEY")

settings = TestSettings()
key = settings.github_app_private_key
print(f"Pydantic key start: {repr(key[:50])}")

try:
    payload = {"test": "data"}
    encoded = jwt.encode(payload, key, algorithm="RS256")
    print("Success with Pydantic!")
except Exception as e:
    print(f"Failed with Pydantic: {e}")
