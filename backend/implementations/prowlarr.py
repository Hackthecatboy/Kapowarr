"""User-triggered, one-way Prowlarr indexer import with explicit ownership."""

import json
from urllib.parse import urlsplit, urlunsplit

from requests import RequestException, Session

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid, InvalidKeyValue
from backend.base.definitions import BrokenClientReason
from backend.internals.db import get_db


def normalize_url(value):
    if not isinstance(value, str):
        raise InvalidKeyValue('url', 'Enter a Prowlarr base URL')
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
        host = parsed.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        if port and (parsed.scheme, port) not in [('http', 80), ('https', 443)]:
            host += ':' + str(port)
        return urlunsplit((parsed.scheme, host, parsed.path.rstrip('/'), '', ''))
    except ValueError:
        raise InvalidKeyValue('url', 'Use HTTP(S) without credentials, query or fragment') from None


def configuration():
    row = get_db().execute('SELECT url, api_token, categories FROM prowlarr_connection WHERE id=1').fetchone()
    return dict(url=row[0], api_token=row[1], categories=json.loads(row[2])) if row else None


def public_configuration():
    saved = configuration()
    return dict(url=saved['url'], has_api_key=True, categories=saved['categories']) if saved else dict(url='', has_api_key=False, categories=[7030])


def validate_configuration(data):
    if not isinstance(data, dict):
        raise InvalidKeyValue('configuration', 'Expected an object')
    url = normalize_url(data.get('url'))
    saved = configuration()
    key = data.get('api_token')
    if not key and saved and saved['url'] == url:
        key = saved['api_token']
    if not isinstance(key, str) or not key.strip() or '\n' in key or '\r' in key:
        raise InvalidKeyValue('api_token', 'Enter a Prowlarr API key')
    categories = data.get('categories', [7030])
    if not isinstance(categories, list) or any(type(c) is not int or c <= 0 for c in categories):
        raise InvalidKeyValue('categories', 'Use positive category IDs')
    # A different instance can reuse the same remote IDs. Do not adopt it silently.
    if saved and saved['url'] != url and get_db().execute('SELECT 1 FROM prowlarr_indexers LIMIT 1').fetchone():
        raise InvalidKeyValue('url', 'Remove imported indexers before replacing the Prowlarr address')
    return dict(url=url, api_token=key.strip(), categories=sorted(set(categories)))


def fetch_indexers(config):
    """Bound JSON responses; never forward keys through redirects or log bodies."""
    try:
        with Session() as session:
            with session.get(config['url'] + '/api/v1/indexer',
                             headers={'X-Api-Key': config['api_token']},
                             timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code in (401, 403):
                    raise CredentialInvalid
                if response.status_code != 200:
                    raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError
                result = json.loads(content)
    except RequestException:
        raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR) from None
    except (ValueError, UnicodeError):
        raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE) from None
    if not isinstance(result, list):
        raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)
    entries = []
    seen = set()
    for item in result:
        if (not isinstance(item, dict) or type(item.get('id')) is not int
                or item['id'] <= 0 or item['id'] in seen
                or not isinstance(item.get('name'), str)
                or type(item.get('enable')) is not bool
                or not isinstance(item.get('protocol'), str)):
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)
        seen.add(item['id'])
        entries.append(dict(id=item['id'], name=item['name'], enabled=item['enable'],
                            protocol=item['protocol']))
    return entries


def preview(data):
    config = validate_configuration(data)
    entries = fetch_indexers(config)
    cursor = get_db()
    managed = dict(cursor.execute('SELECT remote_id, indexer_id FROM prowlarr_indexers').fetchall())
    existing = {normalize_url(row[1]): row[0] for row in cursor.execute(
        "SELECT id, url FROM indexer_clients WHERE client_type IN ('Newznab', 'Torznab')").fetchall()}
    for entry in entries:
        url = config['url'] + '/' + str(entry['id']) + '/api'
        entry['managed'] = entry['id'] in managed
        entry['duplicate'] = url in existing and existing[url] != managed.get(entry['id'])
        entry['supported'] = entry['protocol'] in ('usenet', 'torrent')
    return entries


def synchronize(data):
    config = validate_configuration(data)
    selected = data.get('ids')
    if not isinstance(selected, list) or any(type(i) is not int or i <= 0 for i in selected):
        raise InvalidKeyValue('ids', 'Select indexers from the preview')
    entries = fetch_indexers(config)
    by_id = {entry['id']: entry for entry in entries}
    if any(i not in by_id or by_id[i]['protocol'] not in ('usenet', 'torrent') for i in selected):
        raise InvalidKeyValue('ids', 'Indexer list changed; preview again')
    cursor = get_db()
    counts = dict(created=0, updated=0, disabled=0, skipped=0)
    # Only database work occurs inside the transaction; no partial network imports.
    cursor.execute('SAVEPOINT prowlarr_sync')
    try:
        # Recheck inside the transaction in case the saved connection changed.
        validate_configuration(data)
        managed = dict(cursor.execute('SELECT remote_id, indexer_id FROM prowlarr_indexers').fetchall())
        existing = {normalize_url(row[1]): row[0] for row in cursor.execute(
            "SELECT id, url FROM indexer_clients WHERE client_type IN ('Newznab', 'Torznab')").fetchall()}
        for remote_id in sorted(set(selected)):
            entry = by_id[remote_id]
            url = config['url'] + '/' + str(remote_id) + '/api'
            local_id = managed.get(remote_id)
            if url in existing and existing[url] != local_id:
                counts['skipped'] += 1
                continue
            kind = 'Newznab' if entry['protocol'] == 'usenet' else 'Torznab'
            values = (entry['enabled'], 3 if kind == 'Newznab' else 2, kind,
                      'Prowlarr: ' + entry['name'], url, config['api_token'], json.dumps(config['categories']))
            if local_id is None:
                local_id = cursor.execute('''INSERT INTO indexer_clients
                    (enabled,download_type,client_type,title,url,api_token,categories)
                    VALUES (?,?,?,?,?,?,?)''', values).lastrowid
                cursor.execute('INSERT INTO prowlarr_indexers(remote_id,indexer_id) VALUES (?,?)', (remote_id, local_id))
                counts['created'] += 1
            else:
                cursor.execute('''UPDATE indexer_clients SET enabled=?,download_type=?,client_type=?,title=?,url=?,api_token=?,categories=? WHERE id=?''', (*values, local_id))
                counts['updated'] += 1
        # Missing/unsupported managed entries are retained for queued release references.
        for remote_id, local_id in managed.items():
            entry = by_id.get(remote_id)
            if entry is None or entry['protocol'] not in ('usenet', 'torrent') or not entry['enabled']:
                counts['disabled'] += cursor.execute('UPDATE indexer_clients SET enabled=0 WHERE id=? AND enabled=1', (local_id,)).rowcount
        cursor.execute('''INSERT INTO prowlarr_connection(id,url,api_token,categories)
            VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,
            api_token=excluded.api_token,categories=excluded.categories''',
                       (config['url'], config['api_token'], json.dumps(config['categories'])))
        cursor.execute('RELEASE SAVEPOINT prowlarr_sync')
    except Exception:
        cursor.execute('ROLLBACK TO SAVEPOINT prowlarr_sync')
        cursor.execute('RELEASE SAVEPOINT prowlarr_sync')
        raise
    return counts
