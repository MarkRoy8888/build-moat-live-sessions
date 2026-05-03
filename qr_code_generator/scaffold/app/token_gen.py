import hashlib
import secrets
import string

from sqlalchemy.orm import Session

from .models import UrlMapping
from .settings import settings

BASE62_CHARS = string.ascii_letters + string.digits
MAX_RETRIES = 10


def base62_encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    if num == 0:
        return BASE62_CHARS[0]
    result = []
    while num > 0:
        num, remainder = divmod(num, 62)
        result.append(BASE62_CHARS[remainder])
    return "".join(reversed(result))


def token_exists_in_db(db: Session, token: str) -> bool:
    return db.query(UrlMapping).filter(UrlMapping.token == token).first() is not None


def _hash_to_token(payload: str, length: int) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    encoded = base62_encode(digest)
    return encoded[:length]


def _random_token(length: int) -> str:
    return "".join(secrets.choice(BASE62_CHARS) for _ in range(length))


def generate_token(url: str, db: Session) -> str:
    """Generate a unique token using the strategy configured in settings."""
    strategy = settings.token_strategy
    length = settings.token_length

    if strategy == "random":
        for _ in range(MAX_RETRIES):
            token = _random_token(length)
            if not token_exists_in_db(db, token):
                return token
        raise RuntimeError(f"Could not generate unique token after {MAX_RETRIES} retries")

    if strategy == "hash_only":
        token = _hash_to_token(url, length)
        if token_exists_in_db(db, token):
            raise RuntimeError(
                f"Token collision for URL using hash_only strategy. "
                f"Either the same URL was already submitted, or two URLs hashed to the same {length}-char prefix."
            )
        return token

    if strategy == "hash_with_nonce":
        for attempt in range(MAX_RETRIES):
            payload = f"{url}::{attempt}" if attempt > 0 else url
            token = _hash_to_token(payload, length)
            if not token_exists_in_db(db, token):
                return token
        raise RuntimeError(f"Could not generate unique token after {MAX_RETRIES} retries")

    raise ValueError(f"Unknown token_strategy: {strategy}")


def claim_custom_alias(alias: str, db: Session) -> str:
    """Validate and reserve a user-supplied custom alias."""
    if not alias:
        raise ValueError("Alias is empty")
    if len(alias) > 64:
        raise ValueError("Alias too long (max 64)")
    if not all(c in BASE62_CHARS + "-_" for c in alias):
        raise ValueError("Alias may only contain letters, digits, '-' and '_'")
    if token_exists_in_db(db, alias):
        raise ValueError(f"Alias '{alias}' already taken")
    return alias
