from datetime import date as date_t
from datetime import datetime

from pydantic import BaseModel, Field


class HomeOut(BaseModel):
    id: str
    city: str
    address: str
    type: str
    amenities: list[str]

    class Config:
        from_attributes = True


class SearchHit(BaseModel):
    home_id: str
    city: str
    address: str
    type: str
    amenities: list[str]


class SearchResponse(BaseModel):
    results: list[SearchHit]
    total: int
    page: int
    page_size: int
    elapsed_ms: float
    cache_hit: bool = False
    index_strategy: str


class HomeDetailResponse(BaseModel):
    home: HomeOut
    elapsed_ms: float
    cache_hit: bool


class BookRequest(BaseModel):
    home_id: str
    start_date: date_t
    end_date: date_t
    user_id: str


class BookResponse(BaseModel):
    booking_id: str
    status: str
    expires_at: datetime | None
    elapsed_ms: float
    concurrency_mode: str
    detail: str | None = None


class ConfirmRequest(BaseModel):
    idempotency_key: str | None = None


class ConfirmResponse(BaseModel):
    booking_id: str
    status: str
    elapsed_ms: float
    idempotent_replay: bool = False


class CancelResponse(BaseModel):
    booking_id: str
    status: str
    elapsed_ms: float


class SettingsResponse(BaseModel):
    index_strategy: str
    cache_home_detail: bool
    concurrency_mode: str
    idempotency_enforced: bool
    reservation_ttl_seconds: int


class SettingsUpdate(BaseModel):
    index_strategy: str | None = None
    cache_home_detail: bool | None = None
    concurrency_mode: str | None = None
    idempotency_enforced: bool | None = None
    reservation_ttl_seconds: int | None = Field(None, ge=5, le=3600)


class ExplainResponse(BaseModel):
    sql: str
    plan: list[str]
    index_strategy: str


class BenchResult(BaseModel):
    label: str
    runs: int
    total_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float


class BenchResponse(BaseModel):
    results: list[BenchResult]
