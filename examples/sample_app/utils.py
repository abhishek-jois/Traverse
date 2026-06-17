"""Generic helper utilities."""

from logger import get_logger

log = get_logger(__name__)


def slugify(text):
    log.info("slugify %s", text)
    return text.strip().lower().replace(" ", "-")


def truncate(text, length=80):
    return text if len(text) <= length else text[:length] + "…"
