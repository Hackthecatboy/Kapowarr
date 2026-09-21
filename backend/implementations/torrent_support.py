"""Local torrent identity, bounded transport and managed-client responses."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import BrokenClientReason, Constants
from backend.implementations.managed_job import JobNeedsReview

MAX_BODY = 8 * 1024 * 1024


def invalid():
    return ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)


def http(session, method, url, **kwargs):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise invalid()
    try:
        with session.request(method, url, timeout=(10, 30), stream=True,
                             allow_redirects=False, **kwargs) as response:
            if response.status_code in (401, 403):
                raise CredentialInvalid
            if response.status_code not in (301, 302, 303, 307, 308, 409):
                response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    raise invalid()
            return response.status_code, response.headers, bytes(body)
    except requests.RequestException:
        raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR) from None


def decode_json(response):
    if response[0] != 200:
        raise invalid()
    try:
        return json.loads(response[2])
    except (ValueError, TypeError):
        raise invalid() from None


def session():
    result = requests.Session()
    result.headers['User-Agent'] = Constants.DEFAULT_USERAGENT
    return result


@dataclass
class TorrentPayload:
    info_hash: str
    magnet: str = ''
    metainfo: bytes = b''


def magnet_payload(link):
    hashes = []
    for xt in parse_qs(urlsplit(link).query).get('xt', []):
        if xt.lower().startswith('urn:btih:'):
            value = xt[9:]
            if re.fullmatch(r'[a-fA-F0-9]{40}', value):
                hashes.append(value.lower())
            elif re.fullmatch(r'[a-zA-Z2-7]{32}', value):
                hashes.append(base64.b32decode(value.upper()).hex())
    if len(set(hashes)) != 1:
        raise JobNeedsReview(
            'Torrent requires one valid v1 info hash (v2-only torrents are not supported yet)')
    return TorrentPayload(hashes[0], magnet=link)


def torrent_payload(body):
    # Parse without re-encoding: the hash belongs to the exact info-dictionary
    # bytes. Bound recursion and object count even within a bounded HTTP body.
    pos, count = 0, 0
    info_span = None

    def parse(depth=0):
        nonlocal pos, count, info_span
        count += 1
        if depth > 32 or count > 100000 or pos >= len(body):
            raise ValueError
        token = body[pos:pos+1]
        if token == b'i':
            end = body.index(b'e', pos)
            value = body[pos+1:end]
            if not re.fullmatch(rb'-?(0|[1-9][0-9]*)', value) or len(value) > 20:
                raise ValueError
            pos = end + 1
            return int(value)
        if token in (b'l', b'd'):
            pos += 1
            result = {} if token == b'd' else []
            while body[pos:pos+1] != b'e':
                key = parse(depth + 1)
                if token == b'l':
                    result.append(key)
                else:
                    if not isinstance(key, bytes) or key in result:
                        raise ValueError
                    start = pos
                    result[key] = parse(depth + 1)
                    if depth == 0 and key == b'info':
                        info_span = (start, pos)
            pos += 1
            return result
        end = body.index(b':', pos)
        length = body[pos:end]
        if not re.fullmatch(rb'0|[1-9][0-9]*', length) or len(length) > 9:
            raise ValueError
        size = int(length)
        pos = end + 1
        if pos + size > len(body):
            raise ValueError
        result = body[pos:pos+size]
        pos += size
        return result

    try:
        if len(body) > MAX_BODY:
            raise ValueError
        result = parse()
        info = result[b'info']
        if pos != len(body) or not isinstance(info, dict) or info_span is None:
            raise ValueError
        if not isinstance(info.get(b'pieces'), bytes) or len(info[b'pieces']) % 20 or not info[b'pieces']:
            raise ValueError
        if type(info.get(b'piece length')) is not int or info[b'piece length'] <= 0 or not isinstance(info.get(b'name'), bytes):
            raise ValueError
        if b'files' not in info and (type(info.get(b'length')) is not int or info[b'length'] < 0):
            raise ValueError

        def safe_component(value):
            return isinstance(value, bytes) and value not in (b'', b'.', b'..') and not any(c in value for c in (b'/', b'\\', b'\x00'))
        if not safe_component(info[b'name']) or (b'name.utf-8' in info and not safe_component(info[b'name.utf-8'])):
            raise ValueError
        if b'files' in info:
            if not isinstance(info[b'files'], list) or not info[b'files']:
                raise ValueError
            for file in info[b'files']:
                if not isinstance(file, dict) or type(file.get(b'length')) is not int or file[b'length'] < 0 or b'symlink path' in file or b'l' in file.get(b'attr', b''):
                    raise ValueError
                for key in (b'path', b'path.utf-8'):
                    if key == b'path' or key in file:
                        parts = file.get(key)
                        if not isinstance(parts, list) or not parts or not all(safe_component(p) for p in parts):
                            raise ValueError
        return TorrentPayload(hashlib.sha1(body[info_span[0]:info_span[1]]).hexdigest(), metainfo=body)
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        raise JobNeedsReview(
            'The indexer did not return valid v1/hybrid torrent metadata') from None


def resolve_torrent(link):
    with session() as ssn:
        for _ in range(6):
            if urlsplit(link).scheme.lower() == 'magnet':
                return magnet_payload(link)
            response = http(ssn, 'GET', link)
            if response[0] in (301, 302, 303, 307, 308):
                target = response[1].get('Location')
                if not target:
                    raise invalid()
                link = urljoin(link, target)
                continue
            if response[0] != 200:
                raise invalid()
            return torrent_payload(response[2])
    raise JobNeedsReview('Too many redirects while fetching torrent metadata')
