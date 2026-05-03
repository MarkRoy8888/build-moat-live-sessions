from urllib.parse import urlparse, urlunparse

from .settings import settings

MAX_URL_LENGTH = 2048

BLOCKED_DOMAINS = {
    "evil.com",
    "malware.example.com",
    "phishing.example.com",
}


def is_blocked_domain(hostname: str | None) -> bool:
    if hostname is None:
        return True
    host = hostname.lower().rstrip(".")
    return host in BLOCKED_DOMAINS


def _split_raw_authority(url: str) -> tuple[str, str]:
    """Extract raw scheme and authority from input string (case-preserved)."""
    if "://" not in url:
        return "", ""
    raw_scheme, rest = url.split("://", 1)
    authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in authority:
        authority = authority.split("@", 1)[1]
    return raw_scheme, authority


def validate_url(url: str) -> str:
    """Format check, normalization, and blocklist validation."""
    normalized, _changes = validate_and_describe(url)
    return normalized


def validate_and_describe(url: str) -> tuple[str, list[str]]:
    """Same as validate_url but also returns a list of human-readable changes."""
    if not url or not url.strip():
        raise ValueError("URL is empty")
    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"URL exceeds {MAX_URL_LENGTH} characters")

    parsed = urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http/https schemes allowed (got '{parsed.scheme}')")

    if not parsed.hostname:
        raise ValueError("URL must have a hostname")

    if is_blocked_domain(parsed.hostname):
        raise ValueError(f"Domain '{parsed.hostname}' is blocked")

    changes: list[str] = []
    mode = settings.normalization_mode

    raw_scheme, raw_authority = _split_raw_authority(url)
    raw_host_only = raw_authority.split(":", 1)[0]

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".")

    if raw_scheme and raw_scheme != scheme:
        changes.append(f"scheme lowercased ({raw_scheme} -> {scheme})")
    if raw_host_only and raw_host_only != hostname:
        changes.append(f"hostname normalized ({raw_host_only} -> {hostname})")

    netloc = hostname
    default_ports = {"http": 80, "https": 443}
    if parsed.port and parsed.port != default_ports.get(scheme):
        netloc = f"{hostname}:{parsed.port}"
    elif parsed.port:
        changes.append(f"removed default port :{parsed.port}")

    path = parsed.path
    if path == "":
        path = "/"
        changes.append("empty path -> /")

    query = parsed.query
    fragment = parsed.fragment

    if mode == "aggressive":
        if scheme == "http":
            scheme = "https"
            changes.append("upgraded http -> https")
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/") or "/"
            changes.append("stripped trailing /")
        if path.lower() != path:
            old_path = path
            path = path.lower()
            changes.append(f"path lowercased ({old_path} -> {path})")
        if fragment:
            changes.append(f"removed fragment #{fragment}")
            fragment = ""

    normalized = urlunparse((scheme, netloc, path, parsed.params, query, fragment))
    return normalized, changes
