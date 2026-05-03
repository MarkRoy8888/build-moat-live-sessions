from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CreateRequest(BaseModel):
    url: str
    expires_at: datetime | None = None
    custom_alias: str | None = None


class CreateResponse(BaseModel):
    token: str
    short_url: str
    qr_code_url: str
    original_url: str


class QRInfoResponse(BaseModel):
    token: str
    original_url: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    is_deleted: bool


class UpdateRequest(BaseModel):
    url: str | None = None
    expires_at: datetime | None = None


class QRListItem(BaseModel):
    token: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None
    is_deleted: bool
    scan_count: int


class SettingsResponse(BaseModel):
    token_length: int
    token_strategy: Literal["random", "hash_only", "hash_with_nonce"]
    normalization_mode: Literal["conservative", "aggressive"]
    redirect_status: Literal[301, 302]
    gone_status: Literal[404, 410]


class SettingsUpdate(BaseModel):
    token_length: int | None = None
    token_strategy: Literal["random", "hash_only", "hash_with_nonce"] | None = None
    normalization_mode: Literal["conservative", "aggressive"] | None = None
    redirect_status: Literal[301, 302] | None = None
    gone_status: Literal[404, 410] | None = None


class PreviewRequest(BaseModel):
    url: str


class PreviewResponse(BaseModel):
    original: str
    normalized: str
    changes: list[str]
    valid: bool
    error: str | None = None
