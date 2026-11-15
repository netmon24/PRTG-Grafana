import time

from prtg_collector.portal.setup_runner import SetupRunner
from prtg_collector.setup_tenants import SetupReport


def _wait_idle(runner, timeout=3.0):
    end = time.time() + timeout
    while runner._running and time.time() < end:
        time.sleep(0.01)
    assert not runner._running, "Lauf wurde nicht fertig"


def _runner(outcomes, delays=(5, 15, 30)):
    slept, calls = [], []

    def run():
        calls.append(1)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return SetupReport(problems=outcome)

    runner = SetupRunner(run, retry_delays=delays, sleep=slept.append)
    return runner, calls, slept


def test_success_runs_once_without_waiting():
    runner, calls, slept = _runner([[]])
    runner.trigger()
    _wait_idle(runner)
    assert len(calls) == 1 and slept == []


def test_failure_is_retried_with_growing_waits_until_it_works():
    # InfluxDB oder Grafana starten nach einer Umschaltung noch neu: erst Fehler, dann Erfolg
    runner, calls, slept = _runner([["Influx nicht erreichbar"], OSError("weg"), []])
    runner.trigger()
    _wait_idle(runner)
    assert len(calls) == 3 and slept == [5, 15]
    assert runner.problems() == []


def test_retries_stop_after_the_last_wait_and_problems_stay_visible():
    runner, calls, slept = _runner([["Dauerfehler"]], delays=(1, 2))
    runner.trigger()
    _wait_idle(runner)
    assert len(calls) == 3 and slept == [1, 2]
    assert runner.problems() == ["Dauerfehler"]


def test_a_new_trigger_starts_counting_again():
    runner, calls, slept = _runner([["x"]], delays=(1,))
    runner.trigger()
    _wait_idle(runner)
    assert len(calls) == 2
    runner.trigger()
    _wait_idle(runner)
    assert len(calls) == 4 and slept == [1, 1]


def test_a_new_trigger_during_the_wait_runs_immediately():
    calls = []

    def run():
        calls.append(1)
        return SetupReport(problems=["noch nicht erreichbar"])

    runner = SetupRunner(run, retry_delays=(30,))  # echte Wartezeit, Test darf sie nicht abwarten
    runner.trigger()
    end = time.time() + 2
    while len(calls) < 1 and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.1)  # Lauf ist fertig, der Wiederholungsversuch wartet
    runner.trigger()
    end = time.time() + 2
    while len(calls) < 2 and time.time() < end:
        time.sleep(0.01)
    assert len(calls) >= 2
