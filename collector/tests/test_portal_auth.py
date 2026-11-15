from prtg_collector.portal.auth import Auth
from prtg_collector.portal.store import ConfigStore


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def _auth(tmp_path, clock=None):
    store = ConfigStore(tmp_path / "p.db", "ein-langer-geheimer-schluessel-fuer-tests")
    return Auth(store, "admin", "richtiges-passwort-123", clock or Clock())


def test_login_ok_returns_session_that_checks(tmp_path):
    a = _auth(tmp_path)
    token = a.login("admin", "richtiges-passwort-123")
    assert token and a.check(token) and not a.check("unbekannt")


def test_wrong_password_returns_none(tmp_path):
    assert _auth(tmp_path).login("admin", "falsch") is None


def test_five_failed_logins_lock_for_5_minutes(tmp_path):
    clock = Clock()
    a = _auth(tmp_path, clock)
    for _ in range(5):
        a.login("admin", "falsch")
    assert a.login("admin", "richtiges-passwort-123") is None
    clock.now += 301
    assert a.login("admin", "richtiges-passwort-123")


def test_session_expires_after_8_hours(tmp_path):
    clock = Clock()
    a = _auth(tmp_path, clock)
    token = a.login("admin", "richtiges-passwort-123")
    clock.now += 8 * 3600 + 1
    assert not a.check(token)


def test_change_password_persists_and_old_stops_working(tmp_path):
    a = _auth(tmp_path)
    assert a.change_password("admin", "richtiges-passwort-123", "neues-passwort-4567")
    assert a.login("admin", "richtiges-passwort-123") is None
    assert a.login("admin", "neues-passwort-4567")


def test_change_password_rejects_short_new_password(tmp_path):
    a = _auth(tmp_path)
    assert not a.change_password("admin", "richtiges-passwort-123", "kurz")


def test_failure_state_is_bounded_in_size_and_username_length(tmp_path, monkeypatch):
    """F11: Beliebig viele oder große Benutzernamen dürfen den Speicher nicht füllen."""
    from prtg_collector.portal import auth as auth_mod

    monkeypatch.setattr(auth_mod, "MAX_TRACKED", 20)
    monkeypatch.setattr(auth_mod, "PRUNE_AT", 10)
    a = _auth(tmp_path)
    for i in range(60):
        a.login("admin", "falsch", ip=f"ip{i}-" + "x" * 5000)
    assert len(a._fails) <= auth_mod.MAX_TRACKED
    assert all(len(k) <= auth_mod.MAX_USERNAME_LEN for k in a._fails)


def test_expired_failure_entries_are_pruned(tmp_path):
    from prtg_collector.portal import auth as auth_mod

    clock = Clock()
    a = _auth(tmp_path, clock)
    for i in range(auth_mod.PRUNE_AT + 5):
        a._fails[f"alt{i}"] = (1, clock.now - 1)  # abgelaufen
    a.login("neu", "falsch", ip="neue-ip")
    assert len(a._fails) < 10


def test_unknown_user_costs_the_same_hash_runs_as_known_user(tmp_path, monkeypatch):
    """F30: Bekannter und unbekannter Benutzer kosten genau einen PBKDF2-Lauf."""
    from prtg_collector.portal import auth as auth_mod

    a = _auth(tmp_path)
    calls = []
    real = auth_mod.hashlib.pbkdf2_hmac

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(auth_mod.hashlib, "pbkdf2_hmac", counting)
    a.login("admin", "falsch")
    known = len(calls)
    calls.clear()
    a.login("gibtsnicht", "falsch")
    assert len(calls) == known == 1


def test_change_password_ends_other_sessions_but_keeps_the_current_one(tmp_path):
    a = _auth(tmp_path)
    mine = a.login("admin", "richtiges-passwort-123")
    other = a.login("admin", "richtiges-passwort-123")
    assert a.change_password("admin", "richtiges-passwort-123", "neues-passwort-4567", keep=mine)
    assert a.check(mine) and not a.check(other)


def test_lock_is_per_ip_so_a_stranger_cannot_lock_out_the_admin(tmp_path):
    a = _auth(tmp_path)
    for _ in range(5):
        a.login("admin", "falsch", ip="203.0.113.9")
    assert a.login("admin", "richtiges-passwort-123", ip="203.0.113.9") is None
    assert a.login("admin", "richtiges-passwort-123", ip="198.51.100.7")


def test_several_admins_can_be_added_and_removed(tmp_path):
    a = _auth(tmp_path)
    assert a.add_user("zweite", "zweites-passwort-123")
    assert a.login("zweite", "zweites-passwort-123")
    assert sorted(a.users()) == ["admin", "zweite"]
    assert not a.add_user("zweite", "anderes-passwort-123")  # gibt es schon
    assert not a.add_user("kurz", "kurz")
    assert not a.add_user("Ungültig Name", "zweites-passwort-123")
    session = a.login("zweite", "zweites-passwort-123")
    assert a.delete_user("zweite", current="admin")
    assert not a.check(session) and a.login("zweite", "zweites-passwort-123") is None


def test_own_account_and_last_admin_cannot_be_deleted(tmp_path):
    a = _auth(tmp_path)
    assert not a.delete_user("admin", current="admin")
    a.add_user("zweite", "zweites-passwort-123")
    assert not a.delete_user("zweite", current="zweite")
    assert a.delete_user("zweite", current="admin")
    assert a.users() == ["admin"]


def test_deleted_bootstrap_admin_is_not_recreated_from_the_env_on_restart(tmp_path):
    a = _auth(tmp_path)
    a.add_user("zweite", "zweites-passwort-123")
    assert a.delete_user("admin", current="zweite")
    again = _auth(tmp_path)  # Neustart mit derselben .env
    assert again.users() == ["zweite"]
    assert again.login("admin", "richtiges-passwort-123") is None
