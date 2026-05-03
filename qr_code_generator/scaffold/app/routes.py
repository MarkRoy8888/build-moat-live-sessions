import io
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db
from .models import ScanEvent, UrlMapping
from .schemas import (
    CreateRequest,
    CreateResponse,
    PreviewRequest,
    PreviewResponse,
    QRInfoResponse,
    QRListItem,
    SettingsResponse,
    SettingsUpdate,
    UpdateRequest,
)
from .settings import settings
from .token_gen import claim_custom_alias, generate_token
from .url_validator import validate_and_describe, validate_url

router = APIRouter()

redirect_cache: dict[str, str] = {}

BASE_URL = "http://localhost:8000"


@router.post("/api/qr/create", response_model=CreateResponse)
def create_qr(req: CreateRequest, db: Session = Depends(get_db)):
    try:
        normalized_url = validate_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if req.custom_alias:
        try:
            token = claim_custom_alias(req.custom_alias, db)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        try:
            token = generate_token(normalized_url, db)
        except RuntimeError as e:
            raise HTTPException(status_code=422, detail=str(e))

    mapping = UrlMapping(
        token=token,
        original_url=normalized_url,
        expires_at=req.expires_at,
    )
    db.add(mapping)
    db.commit()

    short_url = f"{BASE_URL}/r/{token}"
    redirect_cache[token] = normalized_url

    return CreateResponse(
        token=token,
        short_url=short_url,
        qr_code_url=f"{BASE_URL}/api/qr/{token}/image",
        original_url=normalized_url,
    )


@router.get("/r/{token}")
def redirect(token: str, request: Request, db: Session = Depends(get_db)):
    """Cache → DB → 404/410 fallback. Status codes honour live settings."""
    redirect_status = settings.redirect_status
    gone_status = settings.gone_status

    if token in redirect_cache:
        _record_scan(token, request, db)
        return RedirectResponse(redirect_cache[token], status_code=redirect_status)

    mapping = db.query(UrlMapping).filter(UrlMapping.token == token).first()

    if mapping is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if mapping.is_deleted:
        raise HTTPException(status_code=gone_status, detail="Gone (deleted)")

    if mapping.expires_at and mapping.expires_at < datetime.utcnow():
        raise HTTPException(status_code=gone_status, detail="Gone (expired)")

    redirect_cache[token] = mapping.original_url
    _record_scan(token, request, db)
    return RedirectResponse(mapping.original_url, status_code=redirect_status)


@router.get("/api/qr/{token}", response_model=QRInfoResponse)
def get_qr_info(token: str, db: Session = Depends(get_db)):
    mapping = _get_mapping_or_404(token, db)
    return mapping


@router.patch("/api/qr/{token}", response_model=QRInfoResponse)
def update_qr(token: str, req: UpdateRequest, db: Session = Depends(get_db)):
    mapping = _get_mapping_or_404(token, db)

    if req.url is not None:
        try:
            mapping.original_url = validate_url(req.url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        redirect_cache.pop(token, None)

    if req.expires_at is not None:
        mapping.expires_at = req.expires_at
        redirect_cache.pop(token, None)

    db.commit()
    db.refresh(mapping)
    return mapping


@router.delete("/api/qr/{token}")
def delete_qr(token: str, db: Session = Depends(get_db)):
    mapping = _get_mapping_or_404(token, db)
    mapping.is_deleted = True
    db.commit()
    redirect_cache.pop(token, None)
    return {"detail": "Deleted"}


@router.get("/api/qr/{token}/image")
def get_qr_image(token: str, db: Session = Depends(get_db)):
    _get_mapping_or_404(token, db)
    short_url = f"{BASE_URL}/r/{token}"

    img = qrcode.make(short_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/api/qr/{token}/analytics")
def get_analytics(token: str, db: Session = Depends(get_db)):
    _get_mapping_or_404(token, db)

    total = db.query(func.count(ScanEvent.id)).filter(ScanEvent.token == token).scalar()

    daily = (
        db.query(
            func.date(ScanEvent.scanned_at).label("date"),
            func.count(ScanEvent.id).label("count"),
        )
        .filter(ScanEvent.token == token)
        .group_by(func.date(ScanEvent.scanned_at))
        .all()
    )

    return {
        "token": token,
        "total_scans": total,
        "scans_by_day": [{"date": str(row.date), "count": row.count} for row in daily],
    }


@router.get("/api/qr", response_model=list[QRListItem])
def list_qr(db: Session = Depends(get_db)):
    mappings = db.query(UrlMapping).order_by(UrlMapping.created_at.desc()).all()
    counts = dict(
        db.query(ScanEvent.token, func.count(ScanEvent.id))
        .group_by(ScanEvent.token)
        .all()
    )
    return [
        QRListItem(
            token=m.token,
            original_url=m.original_url,
            created_at=m.created_at,
            expires_at=m.expires_at,
            is_deleted=m.is_deleted,
            scan_count=counts.get(m.token, 0),
        )
        for m in mappings
    ]


@router.get("/api/settings", response_model=SettingsResponse)
def get_settings():
    return SettingsResponse(**settings.to_dict())


@router.patch("/api/settings", response_model=SettingsResponse)
def update_settings(req: SettingsUpdate):
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        settings.update(**payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    redirect_cache.clear()
    return SettingsResponse(**settings.to_dict())


@router.post("/api/preview", response_model=PreviewResponse)
def preview_url(req: PreviewRequest):
    try:
        normalized, changes = validate_and_describe(req.url)
        return PreviewResponse(
            original=req.url,
            normalized=normalized,
            changes=changes,
            valid=True,
        )
    except ValueError as e:
        return PreviewResponse(
            original=req.url,
            normalized="",
            changes=[],
            valid=False,
            error=str(e),
        )


def _get_mapping_or_404(token: str, db: Session) -> UrlMapping:
    mapping = db.query(UrlMapping).filter(UrlMapping.token == token).first()
    if mapping is None or mapping.is_deleted:
        raise HTTPException(status_code=404, detail="Not Found")
    return mapping


def _record_scan(token: str, request: Request, db: Session):
    event = ScanEvent(
        token=token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    db.add(event)
    db.commit()
