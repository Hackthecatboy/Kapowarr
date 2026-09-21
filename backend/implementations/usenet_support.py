"""Shared bounded transport and status contract for managed Usenet clients."""

import json
from math import isfinite
from urllib.parse import urlsplit

import requests

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import BrokenClientReason, Constants

CATEGORY = 'kapowarr'


def invalid_response():
    return ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)


def request_json(base_url, endpoint, *, params=None, data=None, payload=None, auth=None):
    url = urlsplit(base_url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment or url.username or url.password:
        raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = Constants.DEFAULT_USERAGENT
            with session.request(
                'POST' if data is not None or payload is not None else 'GET',
                base_url.rstrip('/') + endpoint, params=params, data=data,
                json=payload, auth=auth, timeout=(10, 30), stream=True,
                allow_redirects=False
            ) as response:
                if response.status_code in (401, 403):
                    raise CredentialInvalid
                response.raise_for_status()
                if response.is_redirect:
                    raise invalid_response()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise invalid_response()
                return json.loads(body)
    except (requests.RequestException, OSError):
        # Never include request URLs, credentials or remote response bodies.
        raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR) from None
    except (ValueError, TypeError):
        raise invalid_response() from None


def number(value, default=0):
    try:
        result = float(value)
        return result if isfinite(result) and result >= 0 else default
    except (ValueError, TypeError):
        return default


def status(state, size=0, progress=0, storage=None):
    return dict(state=state, size=int(number(size)),
                progress=min(100.0, number(progress)), speed=0,
                storage=storage)


def entries(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise invalid_response()
    return value
