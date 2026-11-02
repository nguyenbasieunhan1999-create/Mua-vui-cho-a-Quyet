"""Cấu hình ứng dụng qua biến môi trường (xem .env.example ở thư mục gốc)."""

import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+asyncpg://tlmn:tlmn@localhost:5432/tlmn"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    allowed_origins: list[str] = ["http://localhost:5173"]

    # Admin bootstrap (tự động tạo Admin khi khởi động trên cloud nếu được cung cấp)
    admin_username: str = "admin"
    admin_password: str | None = None

    # Admin session (ADR-012)
    cookie_secure: bool = False  # bật True ở môi trường có HTTPS
    admin_session_hours: int = 12
    admin_max_failed_logins: int = 10
    admin_lock_minutes: int = 15
    login_rate_limit_per_minute: int = 5

    # Guest + phòng (slice S2)
    guest_token_days: int = 30
    room_create_per_hour: int = 10
    room_lookup_per_minute: int = 30  # chống dò mã phòng
    ws_auth_timeout_seconds: float = 5.0
    ws_max_messages_per_second: int = 10
    ws_max_frame_bytes: int = 4096
    seat_sweep_interval_seconds: float = 5.0

    @model_validator(mode="after")
    def _normalize_cloud_env(self) -> "Settings":
        # Tự động chuyển postgres:// hoặc postgresql:// sang postgresql+asyncpg://
        if self.database_url.startswith("postgres://"):
            object.__setattr__(
                self, "database_url", "postgresql+asyncpg://" + self.database_url.removeprefix("postgres://")
            )
        elif self.database_url.startswith("postgresql://"):
            object.__setattr__(
                self,
                "database_url",
                "postgresql+asyncpg://" + self.database_url.removeprefix("postgresql://"),
            )

        # Tự động nhận diện domain của Render.com (RENDER_EXTERNAL_URL)
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if render_url and render_url.startswith("https://"):
            origins = [o for o in self.allowed_origins if o.startswith("https://")]
            if render_url not in origins:
                origins.append(render_url)
            object.__setattr__(self, "allowed_origins", origins)
            object.__setattr__(self, "cookie_secure", True)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
