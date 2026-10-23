"""Persistent client protocol v1 state for remote dashboard clients.

The dashboard remains the server.  This module only owns pairing tickets and
hashed device credentials; it does not import ``dashboard`` so it can be tested
without starting the HTTP service or touching a device.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

PROTOCOL_VERSION = 1
SERVER_NAME = 'QQ Pet Copilot'
PAIRING_TICKET_TTL_SECONDS = 300
MAX_PAIRING_TICKETS = 64

#: Stable capability names consumed by native shells.  Keep additions
#: backwards-compatible; clients should ignore names they do not understand.
CAPABILITIES = (
    'api.dashboard.read',
    'api.settings.read',
    'api.settings.write',
    'api.scheduler.control',
    'api.screenshot.read',
    'api.logs.read',
    'api.rewards.read',
    'api.adventure.read',
    'api.friends.read',
    'api.plan.read',
    'api.notifications.read',
    'api.adb.read',
    'api.adb.connect',
    'client.pairing',
    'client.devices',
)

CLIENT_PROTOCOL_FILE = (
    Path(__file__).resolve().parent.parent / 'runs' / 'client_protocol.json'
)


class ClientProtocolError(Exception):
    """Base error with a stable code and suggested HTTP status."""

    status = 400
    code = 'client_protocol_error'

    def __init__(self, message: str = 'Client protocol error'):
        super().__init__(message)
        self.message = message


class InvalidPairingTicket(ClientProtocolError):
    status = 401
    code = 'pairing_ticket_invalid'

    def __init__(self):
        super().__init__('Pairing ticket is invalid, expired, or already used')


class ProtocolStoreError(ClientProtocolError):
    status = 500
    code = 'client_protocol_store_error'


def _iso(epoch: float) -> str:
    return (datetime.fromtimestamp(epoch, timezone.utc)
            .isoformat(timespec='seconds')
            .replace('+00:00', 'Z'))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def client_meta(pairing_available: bool = True) -> dict:
    """Public, secret-free protocol metadata."""
    return {
        'ok': True,
        'protocol_version': PROTOCOL_VERSION,
        'server_name': SERVER_NAME,
        'capabilities': list(CAPABILITIES),
        'pairing': {
            'available': bool(pairing_available),
            'ticket_ttl_seconds': PAIRING_TICKET_TTL_SECONDS,
        },
        'auth': {
            'schemes': ['cookie', 'bearer'],
            'device_tokens': True,
        },
    }


class PairingAttemptLimiter:
    """In-memory failure limiter for ticket exchanges.

    Pairing tickets are already high-entropy, but rate limiting still avoids
    turning a compromised network into an unbounded online oracle.
    """

    def __init__(self, limit: int = 8, window: int = 300, clock=time.monotonic):
        self.limit = max(1, int(limit))
        self.window = max(1, int(window))
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _prune_locked(self, identity: str, now: float) -> list[float]:
        stamps = [
            stamp for stamp in self._failures.get(identity, ())
            if now - stamp < self.window
        ]
        if stamps:
            self._failures[identity] = stamps
        else:
            self._failures.pop(identity, None)
        return stamps

    def retry_after(self, identity: str) -> int:
        now = float(self._clock())
        with self._lock:
            stamps = self._prune_locked(identity, now)
            if len(stamps) < self.limit:
                return 0
            return max(1, int(self.window - (now - stamps[-1])))

    def record_failure(self, identity: str) -> None:
        now = float(self._clock())
        with self._lock:
            stamps = self._prune_locked(identity, now)
            stamps.append(now)
            self._failures[identity] = stamps[-self.limit * 2:]

    def clear(self, identity: str | None = None) -> None:
        with self._lock:
            if identity is None:
                self._failures.clear()
            else:
                self._failures.pop(identity, None)


class ClientProtocolStore:
    """Atomic JSON store for tickets and hashed device tokens."""

    VERSION = 1

    def __init__(self, path: Path | str | None = None, *,
                 clock=time.time,
                 ticket_ttl: int = PAIRING_TICKET_TTL_SECONDS):
        self.path = Path(path) if path is not None else CLIENT_PROTOCOL_FILE
        self._clock = clock
        self.ticket_ttl = max(1, int(ticket_ttl))
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict:
        return {'version': ClientProtocolStore.VERSION, 'tickets': {}, 'devices': {}}

    def pairing_available(self) -> bool:
        """Check whether a ticket can be persisted without changing state."""
        probe = self.path.parent
        while not probe.exists():
            if probe == probe.parent:
                return False
            probe = probe.parent
        return probe.is_dir() and os.access(probe, os.W_OK)

    def _load_locked(self) -> dict:
        try:
            raw = self.path.read_text('utf-8')
        except FileNotFoundError:
            return self._empty()
        except OSError as exc:
            raise ProtocolStoreError(f'Unable to read client protocol state: {exc}') from exc
        try:
            state = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ProtocolStoreError('Client protocol state is not valid JSON') from exc
        if not isinstance(state, dict):
            raise ProtocolStoreError('Client protocol state must be an object')
        if state.get('version') != self.VERSION:
            raise ProtocolStoreError('Unsupported client protocol state version')
        if not isinstance(state.get('tickets'), dict) or not isinstance(state.get('devices'), dict):
            raise ProtocolStoreError('Client protocol state is missing required maps')
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return state

    def _save_locked(self, state: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_name(
                f'.{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(state, handle, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
                self.path.chmod(0o600)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError as exc:
            raise ProtocolStoreError(f'Unable to write client protocol state: {exc}') from exc

    def _cleanup_locked(self, state: dict, now: float) -> None:
        tickets = state['tickets']
        for ticket_hash, record in list(tickets.items()):
            try:
                expired = float(record.get('expires_at', 0)) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                tickets.pop(ticket_hash, None)

    @staticmethod
    def _public_device(device_id: str, record: dict) -> dict:
        return {
            'id': device_id,
            'name': str(record.get('name') or ''),
            'platform': str(record.get('platform') or ''),
            'created_at': _iso(float(record.get('created_at') or 0)),
            'last_seen': (_iso(float(record['last_seen']))
                          if record.get('last_seen') else None),
        }

    def create_ticket(self) -> dict:
        ticket = secrets.token_urlsafe(32)
        now = float(self._clock())
        expires_at = now + self.ticket_ttl
        with self._lock:
            state = self._load_locked()
            self._cleanup_locked(state, now)
            tickets = state['tickets']
            if len(tickets) >= MAX_PAIRING_TICKETS:
                oldest = sorted(
                    tickets,
                    key=lambda key: float(tickets[key].get('created_at') or 0),
                )[:len(tickets) - MAX_PAIRING_TICKETS + 1]
                for key in oldest:
                    tickets.pop(key, None)
            ticket_hash = _token_hash(ticket)
            tickets[ticket_hash] = {
                'created_at': now,
                'expires_at': expires_at,
            }
            self._save_locked(state)
        return {
            'ticket': ticket,
            'expires_at': expires_at,
            'expires_at_iso': _iso(expires_at),
            'ticket_ttl_seconds': self.ticket_ttl,
        }

    def pair(self, ticket: str, name: str, platform: str) -> tuple[str, dict]:
        if not isinstance(ticket, str) or not ticket:
            raise InvalidPairingTicket()
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
            raise ClientProtocolError('Device name must be 1-64 characters')
        if (not isinstance(platform, str) or not platform.strip()
                or len(platform.strip()) > 32):
            raise ClientProtocolError('Device platform must be 1-32 characters')

        now = float(self._clock())
        device_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        with self._lock:
            state = self._load_locked()
            self._cleanup_locked(state, now)
            ticket_hash = _token_hash(ticket)
            record = state['tickets'].get(ticket_hash)
            if record is None:
                raise InvalidPairingTicket()
            # The cleanup above normally removes expired tickets.  Keep this
            # explicit check for old/hand-written state and persist the eviction.
            try:
                expired = float(record.get('expires_at', 0)) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                state['tickets'].pop(ticket_hash, None)
                self._save_locked(state)
                raise InvalidPairingTicket()
            state['devices'][device_id] = {
                'token_hash': _token_hash(token),
                'name': name.strip()[:64],
                'platform': platform.strip()[:32],
                'created_at': now,
                'last_seen': None,
            }
            # Consume the ticket in the same atomic write as the device.
            state['tickets'].pop(ticket_hash, None)
            self._save_locked(state)
            public = self._public_device(device_id, state['devices'][device_id])
        return token, public

    def authenticate_token(self, token: str) -> dict | None:
        return self.resolve_device(token)

    def list_devices(self) -> list[dict]:
        with self._lock:
            state = self._load_locked()
            devices = [
                self._public_device(device_id, record)
                for device_id, record in state['devices'].items()
            ]
        devices.sort(key=lambda item: item.get('created_at') or '', reverse=True)
        return devices

    def resolve_device(self, token: str, *, touch: bool = False) -> dict | None:
        """Authenticate a token and optionally update ``last_seen`` atomically."""
        if not isinstance(token, str) or not token:
            return None
        candidate = _token_hash(token)
        now = float(self._clock())
        with self._lock:
            state = self._load_locked()
            for device_id, record in state['devices'].items():
                stored = record.get('token_hash')
                if not isinstance(stored, str) or not hmac.compare_digest(stored, candidate):
                    continue
                if touch:
                    record['last_seen'] = now
                    self._save_locked(state)
                return self._public_device(device_id, record)
        return None

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            state = self._load_locked()
            removed = state['devices'].pop(device_id, None) is not None
            if removed:
                self._save_locked(state)
            return removed


def _normalise_host(host_header: str, server_port: int,
                    scheme: str = 'http') -> str | None:
    raw = (host_header or '').strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        return None
    try:
        parsed = urlsplit('//' + raw)
    except ValueError:
        return None
    if (parsed.scheme or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        return None
    hostname = parsed.hostname
    if not hostname or hostname.lower() in ('0.0.0.0', '::', '::0'):
        return None
    if any(ch in hostname for ch in ' \t\r\n/@\\'):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        # DNS names are valid Host values; only IP literals need IP validation.
        pass
    try:
        port = parsed.port or int(server_port)
    except (TypeError, ValueError):
        return None
    if not (0 < port <= 65535):
        return None
    host = f'[{hostname}]' if ':' in hostname else hostname
    suffix = '' if port == (443 if scheme == 'https' else 80) else f':{port}'
    return f'{scheme}://{host}{suffix}'


def _local_interface_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for family in (socket.AF_INET, socket.AF_INET6):
            for item in socket.getaddrinfo(
                    socket.gethostname(), None, family, socket.SOCK_STREAM):
                addresses.add(item[4][0].split('%', 1)[0])
    except OSError:
        pass
    # A UDP connect does not send a packet, but lets the kernel choose the
    # address for the default route (useful when the hostname resolves only
    # to loopback).
    for family, destination in ((socket.AF_INET, '192.0.2.1'), (socket.AF_INET6, '2001:db8::1')):
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
            sock.connect((destination, 9))
            addresses.add(sock.getsockname()[0].split('%', 1)[0])
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()

    result: list[str] = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.is_link_local:
            # Link-local IPv6 needs a zone id and is not portable in a QR URL.
            continue
        result.append(str(address))
    return sorted(result)


def candidate_base_urls(host_header: str, server_port: int, *,
                        scheme: str = 'http',
                        addresses: list[str] | None = None) -> list[str]:
    """Derive unique candidate origins from Host and local interfaces."""
    candidates: list[str] = []
    host_url = _normalise_host(host_header, server_port, scheme)
    if host_url:
        candidates.append(host_url)
    for address in (_local_interface_addresses() if addresses is None else addresses):
        url = _normalise_host(
            f'[{address}]' if ':' in address else address,
            server_port,
            scheme,
        )
        if url and url not in candidates:
            candidates.append(url)
    return candidates


def pairing_payloads(ticket: str, expires_at: float, base_urls: list[str]) -> dict:
    """Return QR JSON and deep links; all contain only the one-time ticket."""
    expires_iso = _iso(expires_at)
    qr_data = {
        'v': PROTOCOL_VERSION,
        'type': 'qqpet-pair',
        'ticket': ticket,
        'base_urls': list(base_urls),
        'expires_at': expires_iso,
    }
    deep_links = [
        'qqpet://pair?' + urlencode({
            'v': PROTOCOL_VERSION,
            'base': base_url,
            'ticket': ticket,
            'expires': str(int(expires_at)),
        })
        for base_url in base_urls
    ]
    return {
        'qr_payload': json.dumps(qr_data, ensure_ascii=False, separators=(',', ':')),
        'qr_data': qr_data,
        # Keep the link itself as the default pairing payload.  Native clients
        # understand qpet://; the JSON remains available for web QR scanners.
        'payload': deep_links[0] if deep_links else '',
        'deep_link': deep_links[0] if deep_links else '',
        'deep_links': deep_links,
    }
