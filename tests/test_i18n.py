"""i18n catalog tests — ru/en key parity is a hard CI gate."""

from taskshed.i18n import catalog_keys, normalize, t


def test_ru_en_key_parity():
    assert catalog_keys("ru") == catalog_keys("en")


def test_format_params():
    assert t("where_set", "ru", name="Дом") == "Текущее место: Дом"
    assert t("where_set", "en", name="Home") == "Current place: Home"


def test_unknown_key_falls_back_to_key():
    assert t("no_such_key", "ru") == "no_such_key"


def test_normalize():
    assert normalize("en-US") == "en"
    assert normalize("unknown") == "ru"
    assert normalize(None) == "ru"
