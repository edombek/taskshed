"""i18n — ru/en JSON catalogs, key-based lookup with .format() params."""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent
SUPPORTED = ("ru", "en")
DEFAULT_LANG = "ru"


@cache
def _catalog(lang: str) -> dict[str, str]:
    if lang not in SUPPORTED:
        lang = DEFAULT_LANG
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def normalize(lang: str | None) -> str:
    if lang and lang.split("-")[0] in SUPPORTED:
        return lang.split("-")[0]
    return DEFAULT_LANG


def t(key: str, lang: str | None = None, **params) -> str:
    cat = _catalog(normalize(lang))
    text = cat.get(key) or _catalog(DEFAULT_LANG).get(key)
    if text is None:
        # a missing key used to reach the chat as a raw key ("bad"); keep the
        # fallback crash-proof but make the hole visible in the logs
        log.error("missing i18n key: %s", key)
        return key
    return text.format(**params) if params else text


def catalog_keys(lang: str) -> set[str]:
    return set(_catalog(lang))
