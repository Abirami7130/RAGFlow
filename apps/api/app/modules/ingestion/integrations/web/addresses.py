"""Blocked IP and hostname checks for URL ingestion."""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable


def is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return True
    if address.is_private:
        return True
    if address.is_link_local:
        return True
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        if address in ipaddress.ip_network("100.64.0.0/10"):
            return True
        if address in ipaddress.ip_network("0.0.0.0/8"):
            return True
    if isinstance(address, ipaddress.IPv6Address):
        if address in ipaddress.ip_network("fc00::/7"):
            return True
        if address in ipaddress.ip_network("fe80::/10"):
            return True
        if address.ipv4_mapped is not None and is_blocked_ip(address.ipv4_mapped):
            return True
        if address in ipaddress.ip_network("2002::/16") and address.packed[2:6]:
            embedded = ipaddress.IPv4Address(address.packed[2:6])
            if is_blocked_ip(embedded):
                return True
    return False


def parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None
    except Exception:
        # Some Python builds accept unusual forms in ip_address; numeric hosts may still be blocked via DNS.
        return None


def resolve_host(host: str) -> tuple[list[str], list[str]]:
    ipv4: list[str] = []
    ipv6: list[str] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        addr = sockaddr[0]
        if family == socket.AF_INET and addr not in ipv4:
            ipv4.append(addr)
        elif family == socket.AF_INET6 and addr not in ipv6:
            ipv6.append(addr)
    return ipv4, ipv6


def validate_resolved_addresses(addresses: list[str]) -> list[str]:
    parsed = []
    blocked = False
    for text in addresses:
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if is_blocked_ip(ip):
            blocked = True
        else:
            parsed.append(text)
    if blocked:
        from app.domain.extraction import ExtractionError

        raise ExtractionError("ssrf_blocked", "The URL resolves to a blocked network address.")
    if not parsed:
        from app.domain.extraction import ExtractionError

        raise ExtractionError("ssrf_blocked", "The URL has no usable public addresses.")
    return parsed


ResolveFn = Callable[[str], tuple[list[str], list[str]]]


def validate_hostname(host: str, *, resolve: ResolveFn | None = None) -> list[str]:
    literal = parse_ip_literal(host)
    if literal is not None:
        if is_blocked_ip(literal):
            from app.domain.extraction import ExtractionError

            raise ExtractionError("ssrf_blocked", "The URL points to a blocked network address.")
        return [str(literal)]

    resolver = resolve or resolve_host
    ipv4, ipv6 = resolver(host)
    return validate_resolved_addresses(ipv4 + ipv6)