# SPDX-License-Identifier: GPL-3.0
# XML attribute helpers adapted from silasfelinus/Kapowarr at 2a283b1d.
# Modified for Kapowarr v1.3.2 on 2026-09-20: shared Newznab/Torznab
# transport, explicit errors, pagination, bounded responses and capabilities.

"""Newznab/Torznab protocol operations independent of provider persistence."""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from requests import RequestException, Session

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import BrokenClientReason, DownloadType

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT = (10, 30)


def _invalid_response() -> ClientNotWorking:
    return ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower()


def _attributes(item: ET.Element) -> Dict[str, str]:
    """Read attributes regardless of the Newznab/Torznab namespace prefix."""
    result: Dict[str, str] = {}
    for child in item:
        if _local_name(child.tag) != 'attr':
            continue
        name, value = child.get('name'), child.get('value')
        if name and value is not None:
            result[name.lower()] = value
    return result


def _number(value: Optional[str], default: int = -1) -> int:
    try:
        number = int(value or '')
        return number if number >= 0 else default
    except ValueError:
        return default


def parse_xml(content: bytes) -> ET.Element:
    """Parse a bounded feed and propagate API errors instead of hiding them."""
    if len(content) > MAX_RESPONSE_BYTES:
        raise _invalid_response()
    # Feeds do not need DTDs/entities. Strip NULs for UTF-16/32 declarations.
    declarations = content.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise _invalid_response()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise _invalid_response() from None
    if _local_name(root.tag) == 'error':
        code = _number(root.get('code'))
        if 100 <= code < 200:
            raise CredentialInvalid
        raise _invalid_response()
    return root


@dataclass
class ZnabCapabilities:
    search_available: bool
    max_limit: int
    categories: Dict[int, str]


@dataclass
class ZnabRelease:
    title: str
    link: str
    size: int
    published: Optional[datetime]
    seeders: int


@dataclass
class ZnabPage:
    releases: List[ZnabRelease]
    next_page_available: bool


def parse_capabilities(content: bytes) -> ZnabCapabilities:
    """Require a real caps document with a supported generic search endpoint."""
    root = parse_xml(content)
    if _local_name(root.tag) != 'caps':
        raise _invalid_response()
    available = False
    max_limit = 100
    categories: Dict[int, str] = {}
    for element in root.iter():
        name = _local_name(element.tag)
        if name == 'search':
            available = element.get(
                'available', '').lower() in (
                'yes', 'true', '1')
        elif name == 'limits':
            max_limit = _number(element.get('max'), 100) or 100
        elif name in ('category', 'subcat'):
            category = _number(element.get('id'))
            if category > 0:
                categories[category] = element.get('name', '')
    return ZnabCapabilities(available, max_limit, categories)


def parse_feed(
    content: bytes, base_url: str, protocol: DownloadType,
    offset: int, limit: int
) -> ZnabPage:
    """Read RSS results without assuming unqualified XML attribute tags."""
    root = parse_xml(content)
    if _local_name(root.tag) != 'rss':
        raise _invalid_response()
    channel = next((e for e in root if _local_name(e.tag) == 'channel'), None)
    if channel is None:
        raise _invalid_response()
    items = [e for e in channel if _local_name(e.tag) == 'item']
    response = next(
        (e for e in channel if _local_name(
            e.tag) == 'response'), None)
    if response is not None:
        actual_offset = _number(response.get('offset'), offset)
        if actual_offset != offset:
            # Do not loop over page one when a provider ignores pagination.
            raise _invalid_response()
        total = _number(response.get('total'))
    else:
        total = -1
    has_more = bool(items) and (
        offset + len(items) < total if total >= 0 else len(items) >= limit
    )
    releases: List[ZnabRelease] = []
    seen = set()
    for item in items:
        children = {_local_name(e.tag): e for e in item}
        title = (children['title'].text or '').strip(
        ) if 'title' in children else ''
        attrs = _attributes(item)
        enclosure = children.get('enclosure')
        link = enclosure.get('url', '') if enclosure is not None else ''
        if not link and protocol == DownloadType.TORRENT:
            link = attrs.get('magneturl', '')
        if not link and 'link' in children:
            link = children['link'].text or ''
        link = urljoin(base_url, link.strip()) if link.strip() else ''
        scheme = urlsplit(link).scheme.lower()
        allowed = (
            'http',
            'https',
            'magnet') if protocol == DownloadType.TORRENT else (
            'http',
            'https')
        if not title or not link or scheme not in allowed or link in seen:
            continue
        size = _number(attrs.get('size'))
        if size < 0 and enclosure is not None:
            size = _number(enclosure.get('length'))
        published = None
        if 'pubdate' in children:
            try:
                published = parsedate_to_datetime(
                    children['pubdate'].text or '')
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        releases.append(
            ZnabRelease(
                title,
                link,
                size,
                published,
                _number(
                    attrs.get('seeders'))))
        seen.add(link)
    return ZnabPage(releases, has_more)


class ZnabClient:
    """Use a provider's full API URL, including Prowlarr's per-indexer path.

    Call close() when done, or use a context manager. Errors deliberately omit
    request URLs and response bodies, which can contain credentials.
    """

    def __init__(self, url: str, api_key: str, protocol: DownloadType):
        if protocol not in (DownloadType.TORRENT, DownloadType.USENET):
            raise ValueError('Znab requires torrent or Usenet protocol')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Expected an absolute HTTP(S) API URL')
        # Authentication is supplied separately, not embedded in the URL.
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                'Use a clean API URL and the separate API key field')
        self.url, self.api_key, self.protocol = url, api_key, protocol
        self.session = Session()

    def _request(self, params: Dict[str, str]) -> bytes:
        try:
            with self.session.get(
                self.url, params={**params, 'apikey': self.api_key},
                timeout=REQUEST_TIMEOUT, stream=True
            ) as response:
                if response.status_code in (401, 403):
                    raise CredentialInvalid
                if not response.ok:
                    raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)
                content = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise _invalid_response()
                return bytes(content)
        except RequestException:
            raise ClientNotWorking(
                BrokenClientReason.CONNECTION_ERROR) from None

    def capabilities(self) -> ZnabCapabilities:
        return parse_capabilities(self._request({'t': 'caps'}))

    def test(self) -> ZnabCapabilities:
        caps = self.capabilities()
        if not caps.search_available:
            raise _invalid_response()
        return caps

    def search(
        self, query: str = '', categories: Optional[List[int]] = None,
        offset: int = 0, limit: int = 100
    ) -> ZnabPage:
        """Fetch one search page; an empty query requests recent releases."""
        if type(offset) is not int or offset < 0 or type(
            limit) is not int or limit <= 0:
            raise ValueError('Invalid pagination')
        if any(type(c) is not int or c <= 0 for c in categories or []):
            raise ValueError('Invalid category ID')
        params = {
            't': 'search',
            'extended': '1',
            'offset': str(offset),
            'limit': str(limit)}
        if query.strip():
            params['q'] = query.strip()
        if categories:
            params['cat'] = ','.join(str(c) for c in dict.fromkeys(categories))
        return parse_feed(
            self._request(params),
            self.url,
            self.protocol,
            offset,
            limit)

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
