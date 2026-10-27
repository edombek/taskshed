"""i18n catalog tests — ru/en key parity is a hard CI gate."""

import ast
import pathlib

import taskshed
from taskshed.i18n import catalog_keys, normalize, t


def test_ru_en_key_parity():
    assert catalog_keys("ru") == catalog_keys("en")


def test_all_static_t_keys_exist_in_catalogs():
    """Every t("...") literal in the source must exist in both catalogs.

    A typo like t("bad") used to reach the chat as raw text — the lookup
    falls back to the key. This gate catches it before the user does.
    """
    pkg = pathlib.Path(taskshed.__file__).parent
    used: set[str] = set()
    for path in pkg.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "t" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                used.add(node.args[0].value)
    assert len(used) > 50  # the scan must actually find the calls
    for lang in ("ru", "en"):
        missing = used - catalog_keys(lang)
        assert not missing, f"t() keys missing in {lang}: {sorted(missing)}"


def test_format_params():
    assert t("where_set", "ru", name="Дом") == "Текущее место: Дом"
    assert t("where_set", "en", name="Home") == "Current place: Home"


def test_unknown_key_falls_back_to_key():
    assert t("no_such_key", "ru") == "no_such_key"


def test_format_placeholders_match_across_languages():
    """Same {fields} in ru and en, or .format() will KeyError at runtime."""
    import string

    def fields(s: str) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(s) if f}

    import json
    from pathlib import Path

    import taskshed.i18n as i18n
    ru = json.loads((Path(i18n.__file__).parent / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((Path(i18n.__file__).parent / "en.json").read_text(encoding="utf-8"))
    for key, text in ru.items():
        assert fields(text) == fields(en[key]), f"placeholder mismatch: {key}"


def test_normalize():
    assert normalize("en-US") == "en"
    assert normalize("unknown") == "ru"
    assert normalize(None) == "ru"
