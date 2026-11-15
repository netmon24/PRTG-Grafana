import socket

from prtg_collector.dns import DnsCache


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def _cache(reverse=None, forward=None, ttl=3600, clock=None):
    return DnsCache(
        ttl_s=ttl,
        reverse=reverse or (lambda ip: (_ for _ in ()).throw(socket.herror())),
        forward=forward or (lambda n: (_ for _ in ()).throw(socket.gaierror())),
        clock=clock or Clock(),
    )


def test_dns_ip_uses_reverse():
    c = _cache(reverse=lambda ip: ("srv-01.example.invalid", [], [ip]))
    assert c.resolve("10.0.0.1") == "srv-01.example.invalid"


def test_dns_name_uses_forward_check():
    c = _cache(forward=lambda n: "10.0.0.1")
    assert c.resolve("srv-01.example.invalid") == "srv-01.example.invalid"


def test_dns_unresolvable_name_returns_empty():
    assert _cache().resolve("gibt-es-nicht.example.invalid") == ""


def test_dns_failure_returns_empty_and_is_cached():
    calls = []

    def rev(ip):
        calls.append(ip)
        raise socket.herror()

    c = _cache(reverse=rev)
    assert c.resolve("10.0.0.9") == "" and c.resolve("10.0.0.9") == ""
    assert calls == ["10.0.0.9"]


def test_dns_cache_expires_after_ttl():
    clock, calls = Clock(), []

    def rev(ip):
        calls.append(ip)
        return ("a.example.invalid", [], [ip])

    c = _cache(reverse=rev, ttl=60, clock=clock)
    c.resolve("10.0.0.1")
    clock.now += 61
    c.resolve("10.0.0.1")
    assert len(calls) == 2


def test_dns_empty_host_returns_empty():
    assert _cache().resolve("") == ""


def test_dns_several_reverse_names_give_the_same_answer_in_any_order():
    # Mehrere PTR-Einträge: Der "erste" wechselt je nach Antwort des Resolvers. Wechselt der Name,
    # entsteht in InfluxDB eine neue Reihe (dns_name ist ein Tag) und Sensoren zählen doppelt.
    a = _cache(reverse=lambda ip: ("pve.example.invalid", ["gw.example.invalid"], [ip]))
    b = _cache(reverse=lambda ip: ("gw.example.invalid", ["pve.example.invalid"], [ip]))
    assert a.resolve("10.0.0.1") == b.resolve("10.0.0.1") == "gw.example.invalid"


def test_dns_invalid_hosts_never_raise():  # Fehlerrunde F05
    c = DnsCache()
    for host in ("foo..bar", ".x", "a" * 64 + ".example", "a\x00b"):
        assert c.resolve(host) == ""


def test_dns_reverse_value_error_is_empty():
    def boom(ip):
        raise ValueError("x")

    assert _cache(reverse=boom).resolve("10.0.0.1") == ""
