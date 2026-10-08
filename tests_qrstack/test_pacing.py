from datetime import datetime, timezone
from email.utils import format_datetime
import sqlite3
from unittest.mock import Mock

from cryptography.fernet import Fernet
from PIL import Image
import pytest
import requests

from instagrapi.exceptions import ClientThrottledError, PhotoNotUpload, RateLimitError
from qrstack_instagram import Publisher, Vault, PublicationStopped
from qrstack_instagram.pacing import PUBLISH_WINDOW_SECONDS, retry_after_seconds
from qrstack_instagram.platform import PlatformClient, PlatformUnavailable, PlatformStopped, run_loop
from qrstack_instagram.vault import PublicationDeferred


@pytest.fixture
def paced(tmp_path, monkeypatch):
    monkeypatch.setenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", "1")
    monkeypatch.setenv("QRSTACK_TEST_ACCOUNTS", "internal")
    key = Fernet.generate_key()
    vault = Vault(tmp_path / "vault.db", key)
    vault.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}, "user_id": "42"})
    client = Mock(user_id=42)
    client.account_info.return_value = {"user": {"username": "internal", "pk": 42}}
    client.get_settings.return_value = {"cookies": {}}
    client.photo_upload_to_story.return_value = "123"
    factory = Mock(return_value=client)
    image = tmp_path / "story.png"
    Image.new("RGB", (1080, 1920)).save(image)
    yield vault, client, factory, image, key
    vault.close()


def test_daily_budget_survives_restart_and_reconnect(paced, monkeypatch):
    vault, _, factory, image, key = paced
    now = [1_800_000_000.0]
    monkeypatch.setattr("qrstack_instagram.vault.time.time", lambda: now[0])
    publisher = Publisher(vault, factory)
    publisher.publish("internal", "first", image, "https://example.test")
    publisher.connect("internal", "secret")
    restart = Vault(vault.path, key)
    try:
        other = Publisher(restart, factory)
        with pytest.raises(PublicationDeferred) as deferred:
            other.publish("internal", "second", image, "https://example.test")
        assert deferred.value.retry_after_seconds == PUBLISH_WINDOW_SECONDS
        assert restart.get_job("internal", "second") is None
        now[0] += PUBLISH_WINDOW_SECONDS
        other.publish("internal", "second", image, "https://example.test")
        assert factory.call_count == 3  # Two publications, one explicit connect.
    finally:
        restart.close()


def test_atomic_reservation_prevents_second_process_and_backward_clock(paced, monkeypatch):
    vault, _, _, _, key = paced
    now = [1_800_000_000.0]
    monkeypatch.setattr("qrstack_instagram.vault.time.time", lambda: now[0])
    other = Vault(vault.path, key)
    try:
        vault.reserve("internal", "first")
        vault.finish("internal", "first", "PUBLISHED", "123")
        now[0] -= 10
        with pytest.raises(PublicationDeferred):
            other.reserve("internal", "second")
        # Independent accounts do not consume one another's budget.
        other.reserve("different", "first")
    finally:
        other.close()


def test_legacy_jobs_receive_one_durable_upgrade_window(tmp_path, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr("qrstack_instagram.vault.time.time", lambda: now[0])
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs(account TEXT,id TEXT,status TEXT,story TEXT,PRIMARY KEY(account,id))")
        db.execute("INSERT INTO jobs VALUES ('internal','old','PUBLISHED','123')")
    key = Fernet.generate_key()
    vault = Vault(path, key)
    assert vault.publication_delay("internal") == PUBLISH_WINDOW_SECONDS
    vault.close()
    now[0] += 100
    vault = Vault(path, key)
    assert vault.publication_delay("internal") == PUBLISH_WINDOW_SECONDS - 100
    vault.close()


def test_request_buffer_and_pause_after_wait_prevent_next_request(paced):
    vault, client, factory, image, _ = paced
    clock = [0.0]
    sleeps = []
    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
    publisher = Publisher(vault, factory, request_clock=lambda: clock[0], sleep=sleep)
    def upload(*args, before_request, **kwargs):
        before_request()
        before_request()
        publisher.disable_account("internal")
        before_request()
        pytest.fail("Frozen account should not issue another request")
    client.photo_upload_to_story.side_effect = upload
    with pytest.raises(PublicationStopped):
        publisher.publish("internal", "first", image, "https://example.test")
    assert sleeps == [5, 5]
    assert vault.get_job("internal", "first")["status"] == "UNKNOWN"


def test_stop_during_buffer_is_checked_after_sleep(paced):
    vault, client, factory, image, _ = paced
    publisher = Publisher(vault, factory, request_clock=lambda: 0,
                          sleep=lambda _: Publisher(vault).disable_account("internal"))
    def upload(*args, before_request, **kwargs):
        before_request()
        pytest.fail("Stop during sleep must prevent request")
    client.photo_upload_to_story.side_effect = upload
    with pytest.raises(PublicationStopped):
        publisher.publish("internal", "first", image, "https://example.test")
    assert vault.get("internal")["state"] == "FROZEN"


@pytest.mark.parametrize("error_type", [ClientThrottledError, PhotoNotUpload, RateLimitError])
def test_instagram_rate_limit_persists_retry_after_and_never_retries(paced, monkeypatch, error_type):
    vault, client, factory, image, _ = paced
    now = 1_800_000_000.0
    monkeypatch.setattr("qrstack_instagram.publisher.time.time", lambda: now)
    client.photo_upload_to_story.side_effect = error_type("SECRET", response=Mock(status_code=429, headers={"Retry-After": "172800"}))
    publisher = Publisher(vault, factory)
    with pytest.raises(PublicationStopped):
        publisher.publish("internal", "first", image, "https://example.test")
    assert vault.get("internal")["state"] == "COOLDOWN"
    assert vault.get("internal")["retry_not_before"] == now + 172800
    with pytest.raises(PublicationStopped):
        publisher.connect("internal", "secret")
    with pytest.raises(PublicationStopped):
        publisher.verify("internal")
    client.photo_upload_to_story.assert_called_once()
    factory.assert_called_once()


def test_retry_after_accepts_date_and_rejects_invalid_values():
    now = 1_800_000_000.0
    date = format_datetime(datetime.fromtimestamp(now + 700, timezone.utc))
    assert retry_after_seconds({"Retry-After": date}, now=now) == 700
    assert retry_after_seconds({"Retry-After": "120"}, now=now) == 120
    assert retry_after_seconds({"Retry-After": "invalid"}, now=now) == 0
    assert retry_after_seconds({"Retry-After": "-1"}, now=now) == 0


@pytest.mark.parametrize("status", [429, 500, 503])
def test_platform_transport_returns_retry_signal_without_repeating_request(status):
    session = Mock()
    response = Mock(status_code=status, headers={"Retry-After": "600"})
    session.request.return_value = response
    api = PlatformClient("https://worker.example/", "windows", "SECRET", session=session)
    with pytest.raises(PlatformUnavailable) as failure:
        api.next_job()
    assert failure.value.retry_after_seconds == 600
    session.request.assert_called_once()
    response.close.assert_called_once()


def test_platform_tls_failure_is_not_retried():
    session = Mock()
    session.request.side_effect = requests.exceptions.SSLError("SECRET")
    api = PlatformClient("https://worker.example/", "windows", "SECRET", session=session)
    with pytest.raises(PlatformStopped) as failure:
        api.next_job()
    assert not isinstance(failure.value, PlatformUnavailable)
    assert "SECRET" not in str(failure.value)


def test_poll_retry_honors_server_delay_and_resets_after_success():
    runner = Mock()
    runner.run_once.side_effect = [PlatformUnavailable(1000), {"status": "idle"},
                                  PlatformUnavailable(), {"status": "failed_attention"}]
    sleeps = []
    run_loop(runner, loop=True, sleep=sleeps.append, emit=Mock())
    assert sleeps == [1000, 30, 30]


def test_poll_retry_is_bounded_and_once_does_not_retry():
    runner = Mock()
    runner.run_once.side_effect = PlatformUnavailable()
    sleeps = []
    with pytest.raises(PlatformUnavailable):
        run_loop(runner, loop=True, sleep=sleeps.append, emit=Mock())
    assert runner.run_once.call_count == 6
    assert sleeps == [30, 60, 120, 240, 480]
    runner.reset_mock()
    with pytest.raises(PlatformUnavailable):
        run_loop(runner, sleep=sleeps.append, emit=Mock())
    runner.run_once.assert_called_once()
