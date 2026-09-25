"""Explainable release filters and ranking; never infer quality from link tokens."""

import re

from backend.internals.settings import Settings


def evaluate_preferences(result, settings=None):
    settings = settings if settings is not None else Settings().sv
    title = result.get('display_title', '')
    formats = list(settings.download_preferred_formats)
    preferred = list(settings.download_preferred_terms)
    excluded = list(settings.download_excluded_terms)
    minimum = settings.download_min_size_mb
    maximum = settings.download_max_size_mb
    notes = []
    rejection = None
    size = result.get('size', -1)
    if minimum or maximum:
        if not isinstance(size, (int, float)) or size <= 0:
            notes.append('Release size unknown; size limits not applied')
        elif minimum and size < minimum * 1024 * 1024:
            rejection = f'Release is smaller than the {minimum} MiB minimum'
        elif maximum and size > maximum * 1024 * 1024:
            rejection = f'Release exceeds the {maximum} MiB maximum'
    def contains(term):
        return re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', title, re.IGNORECASE) is not None
    blocked = [term for term in excluded if contains(term)]
    if blocked:
        rejection = 'Excluded release term: ' + ', '.join(blocked)
    matched = [term for term in preferred if contains(term)]
    if matched:
        notes.append('Preferred release terms: ' + ', '.join(matched))
    detected = set(re.findall(r'(?i)(?<!\w)(cbz|cbr|pdf)(?!\w)', title))
    detected = {value.lower() for value in detected}
    format_rank = 0
    if formats:
        format_rank = len(formats)
        if len(detected) == 1:
            actual = next(iter(detected))
            if actual in formats:
                format_rank = formats.index(actual)
                notes.append('Preferred format: ' + actual.upper())
            else:
                notes.append('Format outside preference list: ' + actual.upper())
        else:
            notes.append('Format unknown or ambiguous; no format preference bonus')
    if rejection:
        notes.insert(0, rejection)
    return dict(rejection=rejection, rank=[format_rank, -len(matched)], notes=notes)
