import pytest


@pytest.fixture()
def module(load_script):
    return load_script("dash_zertifikate")


def _queries(dash):
    return [t["query"] for p in dash["panels"] for t in p.get("targets", [])]


def test_uid_title_and_file(module):
    dash = module.dashboard()
    assert module.FILE == "11-zertifikate.json"
    assert dash["uid"] == "prtg-zertifikate"
    assert dash["title"].startswith("Zertifikate")


def test_both_channel_languages_are_queried(module):
    text = "\n".join(_queries(module.dashboard()))
    for name in ("Tage bis zum Ablauf", "Days to Expiration", "Selbstsigniert", "Self-Signed"):
        assert name in text
    for name in ("Widerrufen", "Revoked"):
        assert name in text
