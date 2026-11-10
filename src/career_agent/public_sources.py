"""Bounded anonymous HTTPS reads from explicit public provider allowlists."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit


class SourceUnavailable(ValueError):
    pass


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def public_get(url, allowed_hosts, maximum=2 * 1024 * 1024):
    """No redirects, proxies, cookies or caller headers. Pin public DNS + verify TLS."""
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in allowed_hosts
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.fragment or any(ord(c) < 33 for c in url)):
        raise SourceUnavailable('Unsupported source URL')
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)},
                           key=lambda value: (ipaddress.ip_address(value).version, value))
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise SourceUnavailable('Source did not resolve exclusively to public addresses')
        deadline = time.monotonic() + 45
        connection = _PinnedHTTPS(parts.hostname, addresses[0], timeout=15)
        try:
            path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            connection.request('GET', path, headers={'Accept': 'application/json,text/csv,text/plain',
                               'User-Agent': 'CareerAgent/0.5 public-source-check', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            # A redirect is an observation, never authority to contact another URL.
            if response.status != 200:
                return response.status, b'', response.getheader('Content-Type', '')
            size = response.getheader('Content-Length')
            if size and (not size.isdigit() or int(size) > maximum):
                raise SourceUnavailable('Source response exceeds the download limit')
            chunks, count = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceUnavailable('Source response timed out')
                if connection.sock:
                    connection.sock.settimeout(min(15, remaining))
                chunk = response.read(min(65536, maximum + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum:
                    raise SourceUnavailable('Source response exceeds the download limit')
                chunks.append(chunk)
            return response.status, b''.join(chunks), response.getheader('Content-Type', '')
        finally:
            connection.close()
    except (OSError, http.client.HTTPException) as error:
        raise SourceUnavailable('Source could not be reached securely; retry later') from error
