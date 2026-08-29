from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from ..config import get_settings


_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")


def _assert_public_address(value: str, *, field: str) -> None:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(f"{field}解析到了无效地址") from exc
    if not address.is_global:
        raise ValueError(f"{field}不能访问私有、回环、链路本地或保留地址")


def validate_outbound_url(value: str, *, field: str = "API 地址", resolve_dns: bool = False) -> str:
    url = str(value or "").strip()
    if not url:
        raise ValueError(f"{field}不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError(f"{field}只允许 http/https")
    if parsed.username or parsed.password:
        raise ValueError(f"{field}不能包含 URL 用户名或密码")
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError(f"{field}缺少主机名")

    settings = get_settings()
    if parsed.scheme != "https" and not settings.zft_allow_insecure_provider_http:
        raise ValueError(f"{field}必须使用 HTTPS")
    if settings.zft_allow_private_provider_endpoints:
        return url

    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError(f"{field}不能访问本机或私有网络地址")
    try:
        _assert_public_address(host, field=field)
        return url
    except ValueError:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise

    if resolve_dns:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"{field}主机无法解析") from exc
        addresses = {str(info[4][0]).split("%", 1)[0] for info in infos if info and info[4]}
        if not addresses:
            raise ValueError(f"{field}主机无法解析")
        for address in addresses:
            _assert_public_address(address, field=field)
    return url
