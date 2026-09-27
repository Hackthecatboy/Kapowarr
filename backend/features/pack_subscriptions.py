"""Dated GetComics subscriptions and explicitly selected historical releases."""
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.features import pack_downloads as downloads
from backend.internals.db import get_db

SERVICES = ('GetComics', 'MediaFire', 'WeTransfer', 'Pixeldrain')


def search(query, page=1):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 150:
        raise InvalidKeyValue('query', 'Enter a pack search phrase (2–150 characters)')
    if type(page) is not int or not 1 <= page <= 100:
        raise InvalidKeyValue('page', 'Choose a page from 1 to 100')
    with Session() as session:
        response = session.get(f'https://getcomics.org/page/{page}/', params={'s': query.strip()})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    articles = []
    seen = set()
    for article in soup.select('article.post'):
        anchor = article.select_one('.post-title a')
        if anchor is None:
            continue
        try:
            url = downloads.article_url(anchor.get('href'))
        except InvalidKeyValue:
            continue
        if url not in seen:
            articles.append(dict(url=url, title=anchor.get_text(' ', strip=True)))
            seen.add(url)
    return dict(articles=articles, page=page, has_more=bool(soup.select('a.next.page-numbers')))


def listing():
    cursor = get_db()
    return dict(subscriptions=cursor.execute('SELECT * FROM pack_subscriptions ORDER BY id').fetchalldict(),
                releases=cursor.execute('SELECT * FROM pack_subscription_releases ORDER BY rowid DESC LIMIT 100').fetchalldict())


def create(data):
    query = data.get('query')
    label = data.get('link_filter')
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 150:
        raise InvalidKeyValue('query', 'Enter a pack search phrase')
    if not isinstance(label, str) or not 2 <= len(label.strip()) <= 150:
        raise InvalidKeyValue('link_filter', 'Enter identifying text from the desired download link label, such as Marvel')
    if data.get('service') not in SERVICES or type(data.get('automatic')) is not bool:
        raise InvalidKeyValue('subscription', 'Choose a supported service and download mode')
    root = downloads.download_root(data.get('folder'))
    cursor = get_db()
    if cursor.execute('SELECT count(*) FROM pack_subscriptions').fetchone()[0] >= 20:
        raise InvalidKeyValue('subscription', 'Maximum 20 subscriptions')
    cursor.execute('INSERT INTO pack_subscriptions(query,link_filter,service,folder,automatic,created) VALUES(?,?,?,?,?,?)',
                   (query.strip(), label.strip(), data['service'], str(root), data['automatic'], datetime.now(timezone.utc).date().isoformat()))
    cursor.connection.commit()
    return listing()


def toggle(ident, enabled):
    if type(ident) is not int or type(enabled) is not bool:
        raise InvalidKeyValue('subscription', 'Choose a subscription and enabled state')
    cursor = get_db()
    cursor.execute('UPDATE pack_subscriptions SET enabled=? WHERE id=?', (enabled, ident))
    cursor.connection.commit()
    return listing()


def _release_status(ident, article, status, message):
    cursor = get_db()
    cursor.execute('UPDATE pack_subscription_releases SET status=?,message=? WHERE subscription_id=? AND article=?',
                   (status, message, ident, article))
    cursor.connection.commit()


def check():
    cursor = get_db()
    subscriptions = cursor.execute('SELECT * FROM pack_subscriptions WHERE enabled=1').fetchalldict()
    for subscription in subscriptions:
        try:
            # Bounded discovery; older pages are available via explicit historical search.
            for page in range(1, 4):
                result = search(subscription['query'], page)
                for article in result['articles']:
                    date = re.search(r'(20\d{2})[.\-](\d{2})[.\-](\d{2})', article['title'])
                    release_date = '-'.join(date.groups()) if date else ''
                    if not all(term in article['title'].casefold() for term in subscription['query'].casefold().split()):
                        continue
                    try:
                        datetime.strptime(release_date, '%Y-%m-%d')
                        eligible = subscription['created'] <= release_date <= datetime.now(timezone.utc).date().isoformat()
                    except ValueError:
                        eligible = False
                    cursor.execute('INSERT OR IGNORE INTO pack_subscription_releases VALUES(?,?,?,?,?)',
                                   (subscription['id'], article['url'], article['title'], 'pending' if eligible else 'review',
                                    '' if eligible else 'Older or undated release; use Preview to choose it manually'))
                cursor.connection.commit()
                if not result['has_more']:
                    break
            pending = cursor.execute("SELECT * FROM pack_subscription_releases WHERE subscription_id=? AND status='pending' ORDER BY article", (subscription['id'],)).fetchalldict()
            for release in pending:
                article = release['article']
                if cursor.execute('SELECT 1 FROM pack_downloads WHERE article=?', (article,)).fetchone():
                    _release_status(subscription['id'], article, 'tracked', 'A pack job already exists; no repeat download')
                    continue
                if not subscription['automatic']:
                    _release_status(subscription['id'], article, 'review', 'New pack found; preview to choose a download')
                    continue
                if downloads._ACTIVE:
                    break  # Keep pending until the next scheduled check.
                choices = downloads.preview(article)['choices']
                matches = [item for item in choices if item['supported'] and item['service'] == subscription['service']
                           and subscription['link_filter'].casefold() in item['label'].casefold()]
                if len(matches) != 1:
                    _release_status(subscription['id'], article, 'review', 'No unique service/label match; preview and select a link manually')
                    continue
                if not cursor.execute('SELECT enabled FROM pack_subscriptions WHERE id=?', (subscription['id'],)).fetchone()[0]:
                    break
                downloads.start(matches[0]['token'], subscription['folder'])
                _release_status(subscription['id'], article, 'tracked', 'Automatic pack download submitted; imports still require review')
            cursor.execute('UPDATE pack_subscriptions SET last_checked=?,message=? WHERE id=?',
                           (datetime.now(timezone.utc).isoformat(), 'Check completed', subscription['id']))
        except Exception:
            LOGGER.exception('Pack subscription %s check failed', subscription['id'])
            cursor.execute('UPDATE pack_subscriptions SET message=? WHERE id=?', ('Check failed; inspect System Logs', subscription['id']))
        cursor.connection.commit()
