"""Helpers for deriving a client IP from trusted proxy headers."""

from __future__ import annotations

import logging
import os
from ipaddress import ip_address, ip_network
from typing import Optional

from starlette.requests import Request

logger = logging.getLogger(__name__)

DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.0/8",
    "10.0.0.0/8",  # NOSONAR - RFC1918 private proxy range.
    "172.16.0.0/12",  # NOSONAR - RFC1918 private proxy range.
    "192.168.0.0/16",  # NOSONAR - RFC1918 private proxy range.
    "::1/128",
    "fc00::/7",
)


def load_trusted_proxy_networks(env_var: str = "RATE_LIMIT_TRUSTED_PROXIES"):
    raw = os.environ.get(env_var, ",".join(DEFAULT_TRUSTED_PROXY_CIDRS))
    networks = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid %s entry", env_var)
    return tuple(networks)


class ClientIPResolver:
    def __init__(self, trusted_proxy_networks=None):
        self._trusted_proxy_networks = (
            trusted_proxy_networks
            if trusted_proxy_networks is not None
            else load_trusted_proxy_networks()
        )

    def client_ip(self, request: Request) -> str:
        peer_host = request.client.host if request.client else "unknown"
        if not self._is_trusted_proxy(peer_host):
            return peer_host

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            forwarded_ip = self._forwarded_client_ip(forwarded_for)
            if forwarded_ip is not None:
                return forwarded_ip

        real_ip = self._valid_ip_header(request.headers.get("X-Real-IP"))
        if real_ip:
            return real_ip

        return peer_host

    def _forwarded_client_ip(self, forwarded_for: str) -> Optional[str]:
        parsed = self._parse_forwarded_for(forwarded_for)
        for value, parsed_ip in reversed(parsed):
            if not self._is_trusted_ip(parsed_ip):
                return value
        if parsed:
            return parsed[0][0]
        return None

    def _parse_forwarded_for(self, forwarded_for: str) -> list[tuple[str, object]]:
        parsed = []
        for value in forwarded_for.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                parsed.append((value, ip_address(value)))
            except ValueError:
                continue
        return parsed

    def _valid_ip_header(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = value.strip()
        try:
            ip_address(value)
        except ValueError:
            return None
        return value

    def _is_trusted_proxy(self, host: str) -> bool:
        try:
            return self._is_trusted_ip(ip_address(host))
        except ValueError:
            return False

    def _is_trusted_ip(self, parsed_ip) -> bool:
        return any(parsed_ip in network for network in self._trusted_proxy_networks)
