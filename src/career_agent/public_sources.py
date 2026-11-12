"""Bounded anonymous HTTPS reads from explicit public provider allowlists."""
from __future__ import annotations

import http.client
import hashlib
import ipaddress
import socket
import ssl
import time
from threading import Event, Timer
from urllib.parse import urlsplit


class SourceUnavailable(ValueError):
    pass


RESPONSE_DEADLINE = 45


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
        deadline = time.monotonic() + RESPONSE_DEADLINE
        connection = _PinnedHTTPS(parts.hostname, addresses[0], timeout=15)
        timer = None
        response = None
        expired = Event()
        try:
            connection.connect()
            # Keep the TLS socket even when HTTPConnection transfers ownership to
            # HTTPResponse for Connection: close. shutdown interrupts header reads
            # and slow-trickle/chunk-framing reads, not just idle socket reads.
            transport = connection.sock
            def abort_at_deadline():
                expired.set()
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceUnavailable('Source response timed out')
            timer = Timer(remaining, abort_at_deadline)
            timer.daemon = True
            timer.start()
            path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            connection.request('GET', path, headers={'Accept': 'application/json,text/csv,text/plain',
                               'User-Agent': 'CareerAgent/0.5 public-source-check', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if expired.is_set():
                raise SourceUnavailable('Source response timed out')
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
                chunk = response.read1(min(65536, maximum + 1 - count))
                if expired.is_set():
                    raise SourceUnavailable('Source response timed out')
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum:
                    raise SourceUnavailable('Source response exceeds the download limit')
                chunks.append(chunk)
            if size and count != int(size):
                raise SourceUnavailable('Source response was incomplete; existing records retained')
            return response.status, b''.join(chunks), response.getheader('Content-Type', '')
        finally:
            if timer:
                timer.cancel()
                timer.join()
            if response is not None:
                response.close()
            connection.close()
    except (OSError, http.client.HTTPException) as error:
        raise SourceUnavailable('Source could not be reached securely; retry later') from error


def public_download(url, allowed_hosts, destination, *, maximum, progress=None, deadline_seconds=1200):
    """Stream a bounded public HTTPS response to a caller-owned temporary file.

    No redirects, proxies, credentials or caller headers are used. TLS verifies
    the requested hostname, and the socket connects only to a pinned public IP.
    """
    parts = urlsplit(url)
    try:
        valid = (parts.scheme == 'https' and parts.hostname in allowed_hosts and parts.port in (None, 443)
                 and not parts.username and not parts.password and not parts.fragment and not parts.query
                 and not any(ord(c) < 33 for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise SourceUnavailable('Unsupported source URL')
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)},
                           key=lambda value: (ipaddress.ip_address(value).version, value))
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise SourceUnavailable('Source did not resolve exclusively to public addresses')
        deadline = time.monotonic() + deadline_seconds
        connection = _PinnedHTTPS(parts.hostname, addresses[0], timeout=15)
        timer = None
        response = None
        expired = Event()
        try:
            connection.connect()
            transport = connection.sock
            def abort():
                expired.set()
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = Timer(max(0, deadline - time.monotonic()), abort)
            timer.daemon = True
            timer.start()
            connection.request('GET', parts.path or '/', headers={
                'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream',
                'User-Agent': 'CareerAgent/0.5 public-source-check', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if expired.is_set():
                raise SourceUnavailable('Source response timed out')
            if response.status != 200:
                raise SourceUnavailable(f'DOL returned HTTP {response.status}; no records were changed. Download the workbook manually and import it locally if available.')
            size = response.getheader('Content-Length')
            if size and (not size.isdigit() or int(size) > maximum):
                raise SourceUnavailable('Source response exceeds the download limit')
            digest, count = hashlib.sha256(), 0
            with open(destination, 'xb') as target:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceUnavailable('Source response timed out')
                    if connection.sock:
                        connection.sock.settimeout(min(15, remaining))
                    chunk = response.read1(min(65536, maximum + 1 - count))
                    if expired.is_set():
                        raise SourceUnavailable('Source response timed out')
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > maximum:
                        raise SourceUnavailable('Source response exceeds the download limit')
                    digest.update(chunk)
                    target.write(chunk)
                    if progress:
                        progress('downloading', count)
            if not count or (size and count != int(size)):
                raise SourceUnavailable('Source response was incomplete; existing records retained')
            return {'bytes': count, 'sha256': digest.hexdigest()}
        finally:
            if timer:
                timer.cancel()
                timer.join()
            if response is not None:
                response.close()
            connection.close()
    except (OSError, http.client.HTTPException) as error:
        raise SourceUnavailable('DOL download or local staging failed; check connectivity and disk space, then retry') from error
