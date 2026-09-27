"""Dated GetComics subscriptions and explicitly selected historical releases."""
import re
from datetime import datetime, timezone
from typing import Any, List, Mapping, TypedDict

from bs4 import BeautifulSoup

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import PackSubscription, PackSubscriptionListing
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.features import pack_downloads as downloads
from backend.internals.db import KapowarrCursor, get_db
from backend.internals.db_models import PackDownloadsDB, PackSubscriptionsDB

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
    return {
        'subscriptions': PackSubscriptionsDB.fetch(),
        'releases': PackSubscriptionsDB.releases()
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
    if PackSubscriptionsDB.count() >= 20:
        raise InvalidKeyValue('subscription', 'Maximum 20 subscriptions')
    PackSubscriptionsDB.add(
        query.strip(), label.strip(), data['service'], str(root),
        data['automatic'], datetime.now(
            timezone.utc).date().isoformat(), weekday
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
    PackSubscriptionsDB.set_enabled(ident, enabled)
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
    PackSubscriptionsDB.set_weekday(ident, weekday)
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
    PackSubscriptionsDB.set_release_status(ident, article, status, message)
    cursor.connection.commit()


def _claim_check(
    subscription: PackSubscription, force: bool, cursor: KapowarrCursor
) -> bool:
    """Persist a due scheduled attempt before network access.

    Manual checks bypass the schedule without consuming a weekly attempt.
    Database failures propagate outside the per-subscription network handler,
    preserving the existing recovery boundary.
    """
    now = datetime.now()  # Follow the server/container timezone.
    today = now.date().isoformat()
    if not force:
        if (now.weekday() != subscription['weekday']
                or subscription['last_scheduled'] == today):
            return False
        # Persist before network access so restarts and failures do not
        # turn a weekly check into hourly requests for the rest of the day.
        PackSubscriptionsDB.mark_scheduled(subscription['id'], today)
        cursor.connection.commit()
    return True


def _discover_releases(
    subscription: PackSubscription, cursor: KapowarrCursor
) -> None:
    """Record at most three search pages, committing each completed page."""
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
            PackSubscriptionsDB.record_release(
                subscription['id'], article['url'], article['title'],
                'pending' if eligible else 'review',
                '' if eligible else (
                    'Older or undated release; '
                    'use Preview to choose it manually'
                )
            )
        cursor.connection.commit()
        if not result['has_more']:
            break


def _process_pending(subscription: PackSubscription) -> None:
    """Review or submit pending releases, stopping when busy or paused.

    Each release decision commits through _release_status. Provider errors
    propagate to the per-subscription handler; pending releases remain saved.
    """
    pending = PackSubscriptionsDB.pending(subscription['id'])
    for release in pending:
        article = release['article']
        if PackDownloadsDB.article_has_download(article):
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
        if not PackSubscriptionsDB.is_enabled(subscription['id']):
            break
        downloads.start(matches[0]['token'], subscription['folder'])
        _release_status(
            subscription['id'],
            article, 'tracked',
            'Automatic pack download submitted; '
            'imports still require review'
        )


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
    subscriptions = PackSubscriptionsDB.fetch(enabled_only=True)
    for subscription in subscriptions:
        if not _claim_check(subscription, force, cursor):
            continue
        try:
            _discover_releases(subscription, cursor)
            _process_pending(subscription)
            PackSubscriptionsDB.mark_checked(
                subscription['id'], datetime.now(timezone.utc).isoformat(),
                'Check completed'
            )
        except Exception:
            LOGGER.exception(
                'Pack subscription %s check failed',
                subscription['id'])
            PackSubscriptionsDB.set_message(
                subscription['id'], 'Check failed; inspect System Logs'
            )
        cursor.connection.commit()
