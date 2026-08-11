"""Authentication and request throttling helpers for the add-on HTTP API."""

from __future__ import annotations

from collections import defaultdict, deque
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import stat
from time import monotonic
from typing import Mapping


API_VERSION = 2
DEFAULT_TOKEN_PATH = "/homeassistant/.qtronic_sms_gateway/api_token"
DEFAULT_PROXY_NETWORKS = "172.30.32.1/32,172.30.32.2/32,127.0.0.0/8,::1/128"


def load_or_create_api_token(path: str | Path | None = None) -> str:
    """Load the persistent API token, creating a 256-bit token when absent."""
    token_path = Path(
        path or os.environ.get("QTRONIC_API_TOKEN_PATH", DEFAULT_TOKEN_PATH)
    )
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError(f"API token in {token_path} is unexpectedly short.")
        _restrict_permissions(token_path)
        return token

    token = secrets.token_urlsafe(32)
    temporary = token_path.with_name(f".{token_path.name}.{secrets.token_hex(6)}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, token_path)
        _restrict_permissions(token_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return token


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Some mounted Home Assistant filesystems do not expose POSIX permissions.
        pass


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.environ.get("QTRONIC_SUPERVISOR_PROXY_NETWORKS", DEFAULT_PROXY_NETWORKS)
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        try:
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(networks)


def is_supervisor_ingress_request(
    headers: Mapping[str, str],
    client_host: str | None,
) -> bool:
    """Return true only for requests carrying ingress headers from the proxy subnet."""
    normalized = _normalized_headers(headers)
    has_ingress_marker = any(
        name in normalized
        for name in ("x-ingress-path", "x-ingress-entry", "x-hassio-ingress")
    )
    if not has_ingress_marker or not client_host:
        return False
    try:
        address = ipaddress.ip_address(client_host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in _proxy_networks())


class APIAuthenticator:
    """Constant-time bearer authentication plus a bounded per-client rate limit."""

    def __init__(
        self,
        token: str,
        *,
        require_auth: bool = True,
        allow_ingress: bool = True,
        rate_limit_requests: int = 60,
        rate_limit_window_s: int = 60,
    ) -> None:
        self._token = token
        self.require_auth = require_auth
        self.allow_ingress = allow_ingress
        self.rate_limit_requests = max(1, int(rate_limit_requests))
        self.rate_limit_window_s = max(1, int(rate_limit_window_s))
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def authorized(self, headers: Mapping[str, str], client_host: str | None) -> bool:
        if not self.require_auth:
            return True
        if self.allow_ingress and is_supervisor_ingress_request(headers, client_host):
            return True
        supplied = bearer_token(_normalized_headers(headers).get("authorization"))
        return supplied is not None and hmac.compare_digest(supplied, self._token)

    def allow_request(self, client_key: str) -> bool:
        now = monotonic()
        cutoff = now - self.rate_limit_window_s
        bucket = self._requests[client_key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.rate_limit_requests:
            return False
        bucket.append(now)
        if len(self._requests) > 4096:
            self._requests = defaultdict(
                deque,
                {key: value for key, value in self._requests.items() if value},
            )
        return True
