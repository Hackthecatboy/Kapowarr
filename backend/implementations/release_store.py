"""Remember indexer-supplied metadata so enqueue need not trust browser fields."""

import json
from time import time

from backend.internals.db import get_db


def remember_release(result):
    cursor = get_db()
    cursor.execute('DELETE FROM indexer_releases WHERE fetched_at < ?',
                   (int(time()) - 30 * 86400,))
    cursor.execute('''INSERT INTO indexer_releases(indexer_id, link, release_data, fetched_at)
        VALUES (?, ?, ?, ?) ON CONFLICT(indexer_id, link) DO UPDATE SET
        release_data = excluded.release_data, fetched_at = excluded.fetched_at''',
                   (result['indexer_id'], result['link'], json.dumps(result), int(time())))


def get_release(indexer_id, link):
    row = get_db().execute('SELECT release_data FROM indexer_releases WHERE indexer_id = ? AND link = ?',
                           (indexer_id, link)).fetchone()
    if not row:
        return None
    result = json.loads(row[0])
    if isinstance(result.get('issue_number'), list):
        result['issue_number'] = tuple(result['issue_number'])
    if isinstance(result.get('volume_number'), list):
        result['volume_number'] = tuple(result['volume_number'])
    return result
