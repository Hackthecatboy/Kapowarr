"""Dated GetComics subscriptions and explicitly selected historical releases."""
import re
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, TypedDict

from bs4 import BeautifulSoup

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.features import pack_downloads as downloads
from backend.internals.db import get_db

SERVICES = ('GetComics', 'MediaFire', 'WeTransfer', 'Pixeldrain')


class PackArticle(TypedDict):
    """A GetComics article returned by historical search."""

    url: str
    title: str


class PackSearchResult(TypedDict):
    """One page of historical articles and its pagination state."""

    articles: List[PackArticle]
    page: int
    has_more: bool


class PackSubscription(TypedDict):
    """Persisted subscription configuration and check timestamps."""

    id: int
    query: str
    link_filter: str
    service: str
    folder: str
    automatic: int
    enabled: int
    created: str
    last_checked: Optional[str]
    message: str
    weekday: int
    last_scheduled: Optional[str]


class PackRelease(TypedDict):
    """An article discovered for a subscription and its processing state."""

    subscription_id: int
    article: str
    title: str
    status: str
    message: str


class PackSubscriptionListing(TypedDict):
    """Subscriptions and the most recently recorded release history."""

    subscriptions: List[PackSubscription]
    releases: List[PackRelease]


def search(query: object, page: object = 1) -> PackSearchResult:
    """Search a bounded page of GetComics articles.

    Args:
        query: Search phrase supplied by the API or saved subscription.
        page: One-based page number, between 1 and 100.

    Raises:
        InvalidKeyValue: The query or page is invalid.
        requests.RequestException: The remote search request fails.

    Returns:
        Articles, the requested page number and whether another page exists.
    """
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 150:
        raise InvalidKeyValue(
            'query', 'Enter a pack search phrase (2–150 characters)')
    if type(page) is not int or not 1 <= page <= 100:
        raise InvalidKeyValue('page', 'Choose a page from 1 to 100')
    with Session() as session:
        response = session.get(
            f'https://getcomics.org/page/{page}/',
            params={'s': query.strip()}
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    articles: List[PackArticle] = []
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
            articles.append({
                'url': url,
                'title': anchor.get_text(' ', strip=True)
            })
            seen.add(url)
    return {
        'articles': articles,
        'page': page,
        'has_more': bool(soup.select('a.next.page-numbers'))
    }


def listing() -> PackSubscriptionListing:
    """Return all subscriptions and the latest 100 recorded releases."""
    cursor = get_db()
    return {
        'subscriptions': cursor.execute(
            'SELECT * FROM pack_subscriptions ORDER BY id'
        ).fetchalldict(),
        'releases': cursor.execute(
            'SELECT * FROM pack_subscription_releases '
            'ORDER BY rowid DESC LIMIT 100'
        ).fetchalldict()
    }


def create(data: Mapping[str, Any]) -> PackSubscriptionListing:
    """Validate and save a subscription without starting a download.

    Args:
        data: API fields for the query, link filter, service, folder,
            automatic-download mode and optional weekday (Monday is 0).

    Raises:
        InvalidKeyValue: A field is invalid, the folder is unsuitable or
            the subscription limit has been reached.

    Returns:
        The updated subscription listing.
    """
    query = data.get('query')
    label = data.get('link_filter')
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 150:
        raise InvalidKeyValue('query', 'Enter a pack search phrase')
    if not isinstance(label, str) or not 2 <= len(label.strip()) <= 150:
        raise InvalidKeyValue(
            'link_filter',
            'Enter identifying text from the desired download link label, '
            'such as Marvel'
        )
    if (data.get('service') not in SERVICES
            or type(data.get('automatic')) is not bool):
        raise InvalidKeyValue(
            'subscription', 'Choose a supported service and download mode'
        )
    weekday = data.get('weekday', datetime.now().weekday())
    _validate_weekday(weekday)
    root = downloads.download_root(data.get('folder'))
    cursor = get_db()
    if cursor.execute(
        'SELECT count(*) FROM pack_subscriptions').fetchone()[0] >= 20:
        raise InvalidKeyValue('subscription', 'Maximum 20 subscriptions')
    cursor.execute(
        'INSERT INTO pack_subscriptions('
        'query,link_filter,service,folder,automatic,created,weekday'
        ') VALUES(?,?,?,?,?,?,?)',
        (
            query.strip(), label.strip(), data['service'], str(root),
            data['automatic'],
            datetime.now(timezone.utc).date().isoformat(), weekday
        )
    )
    cursor.connection.commit()
    return listing()


def toggle(ident: object, enabled: object) -> PackSubscriptionListing:
    """Pause or resume future checks without cancelling active downloads.

    Args:
        ident: Subscription database ID.
        enabled: Whether future checks are enabled.

    Raises:
        InvalidKeyValue: The ID or enabled flag has an invalid type.

    Returns:
        The updated subscription listing.
    """
    if type(ident) is not int or type(enabled) is not bool:
        raise InvalidKeyValue(
            'subscription', 'Choose a subscription and enabled state'
        )
    cursor = get_db()
    cursor.execute(
        'UPDATE pack_subscriptions SET enabled=? WHERE id=?', (enabled, ident))
    cursor.connection.commit()
    return listing()


def _validate_weekday(weekday: object) -> None:
    """Raise InvalidKeyValue unless weekday is an integer from 0 to 6."""
    if type(weekday) is not int or not 0 <= weekday <= 6:
        raise InvalidKeyValue(
            'weekday', 'Choose a weekday from Monday to Sunday')


def schedule(ident: object, weekday: object) -> PackSubscriptionListing:
    """Change a subscription's weekly check day in the server timezone.

    Args:
        ident: Subscription database ID.
        weekday: Integer from Monday (0) through Sunday (6).

    Raises:
        InvalidKeyValue: The ID or weekday is invalid.

    Returns:
        The updated subscription listing.
    """
    _validate_weekday(weekday)
    if type(ident) is not int:
        raise InvalidKeyValue('subscription', 'Choose a subscription')
    cursor = get_db()
    cursor.execute(
        'UPDATE pack_subscriptions SET weekday=? WHERE id=?', (weekday, ident))
    cursor.connection.commit()
    return listing()


def _release_status(
    ident: int,
    article: str,
    status: str,
    message: str
) -> None:
    """Commit the processing state for one subscription/article pair."""
    cursor = get_db()
    cursor.execute(
        'UPDATE pack_subscription_releases SET status=?,message=? '
        'WHERE subscription_id=? AND article=?',
        (status, message, ident, article))
    cursor.connection.commit()


def check(force: bool = False) -> None:
    """Discover releases and process eligible pending downloads.

    Args:
        force: Check enabled subscriptions regardless of their weekday.
            Manual checks do not consume the scheduled weekly attempt.

    Scheduled attempts are persisted before contacting GetComics. Each check
    searches at most three pages and requires one unique service/label match
    before submitting a download. Active downloads leave pending releases for
    a later check. Errors are logged and recorded per subscription so one
    failed subscription does not stop the others.
    """
    cursor = get_db()
    subscriptions: List[PackSubscription] = cursor.execute(
        'SELECT * FROM pack_subscriptions WHERE enabled=1').fetchalldict()
    for subscription in subscriptions:
        now = datetime.now()  # Follow the server/container timezone.
        today = now.date().isoformat()
        if not force:
            if (now.weekday() != subscription['weekday']
                    or subscription['last_scheduled'] == today):
                continue
            # Persist before network access so restarts and failures do not
            # turn a weekly check into hourly requests for the rest of the day.
            cursor.execute(
                'UPDATE pack_subscriptions SET last_scheduled=? WHERE id=?',
                (today, subscription['id']))
            cursor.connection.commit()
        try:
            # Bounded discovery; older pages are available via explicit
            # historical search.
            for page in range(1, 4):
                result = search(subscription['query'], page)
                for article in result['articles']:
                    date = re.search(
                        r'(20\d{2})[.\-](\d{2})[.\-](\d{2})',
                        article['title'])
                    release_date = '-'.join(date.groups()) if date else ''
                    if not all(
                        term in article['title'].casefold()
                        for term in subscription['query'].casefold().split()):
                        continue
                    try:
                        datetime.strptime(release_date, '%Y-%m-%d')
                        eligible = (
                            subscription['created'] <= release_date
                            <= datetime.now(timezone.utc).date().isoformat()
                        )
                    except ValueError:
                        eligible = False
                    cursor.execute(
                        'INSERT OR IGNORE INTO pack_subscription_releases '
                        'VALUES(?,?,?,?,?)',
                        (subscription['id'],
                         article['url'],
                         article['title'],
                         'pending' if eligible else 'review',
                         '' if eligible else (
                             'Older or undated release; '
                             'use Preview to choose it manually'
                        ))
                    )
                cursor.connection.commit()
                if not result['has_more']:
                    break
            pending = cursor.execute(
                "SELECT * FROM pack_subscription_releases "
                "WHERE subscription_id=? AND status='pending' ORDER BY article",
                (subscription['id'],)).fetchalldict()
            for release in pending:
                article = release['article']
                if cursor.execute(
                    'SELECT 1 FROM pack_downloads WHERE article=?',
                        (article,)).fetchone():
                    _release_status(
                        subscription['id'],
                        article, 'tracked',
                        'A pack job already exists; no repeat download')
                    continue
                if not subscription['automatic']:
                    _release_status(
                        subscription['id'],
                        article, 'review',
                        'New pack found; preview to choose a download')
                    continue
                if downloads.has_active_download():
                    break  # Keep pending until the next scheduled check.
                choices = downloads.preview(article)['choices']
                matches = [
                    item for item in choices
                    if item['supported']
                    and item['service'] == subscription['service']
                    and subscription['link_filter'].casefold()
                    in item['label'].casefold()
                ]
                if len(matches) != 1:
                    _release_status(
                        subscription['id'],
                        article, 'review',
                        'No unique service/label match; '
                        'preview and select a link manually'
                    )
                    continue
                if not cursor.execute(
                    'SELECT enabled FROM pack_subscriptions WHERE id=?',
                        (subscription['id'],)).fetchone()[0]:
                    break
                downloads.start(matches[0]['token'], subscription['folder'])
                _release_status(
                    subscription['id'],
                    article, 'tracked',
                    'Automatic pack download submitted; '
                    'imports still require review'
                )
            cursor.execute(
                'UPDATE pack_subscriptions SET last_checked=?,message=? '
                'WHERE id=?',
                (datetime.now(timezone.utc).isoformat(),
                 'Check completed', subscription['id'])
            )
        except Exception:
            LOGGER.exception(
                'Pack subscription %s check failed',
                subscription['id'])
            cursor.execute(
                'UPDATE pack_subscriptions SET message=? WHERE id=?',
                ('Check failed; inspect System Logs', subscription['id']))
        cursor.connection.commit()
