from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock

from cryptography.fernet import Fernet
from PIL import Image
import pytest

from qrstack_instagram import Publisher, Vault
from qrstack_instagram.platform import (
    MAX_MEDIA_BYTES, PlatformClient, PlatformRunner, PlatformStopped,
    load_account_map, main, runner_lock,
)


def image_bytes(size=(1080, 1920)):
    output = BytesIO()
    Image.new("RGB", size, (27, 41, 32)).save(output, format="PNG")
    return output.getvalue()


class FakePlatform:
    scope = "local-worker-and-publisher"

    def __init__(self):
        self.content = image_bytes()
        self.job = {
            "id": "job-1", "restaurant_slug": "demo", "instagram_username": "internal",
            "instagram_user_id": "42", "story_link": "https://menu.example/demo",
            "media_url": "https://worker.example/?action=getInstagramStoryMedia",
            "media_sha256": hashlib.sha256(self.content).hexdigest(),
            "status": "claimed", "checkpoint": "claimed", "claim_token": "SECRET_CLAIM_TOKEN",
        }
        self.enabled = True
        self.updates = []
        self.downloads = 0
        self.next_calls = 0
        self.fail_ack_once = False
        self.fail_permit_once = False

    def next_job(self):
        self.next_calls += 1
        return {"ok": True, "job": deepcopy(self.job)}

    def inspect(self, job):
        return {"ok": True, "job": deepcopy(self.job), "can_publish": self.enabled,
                "publishing": {"enabled": self.enabled}}

    def update(self, job, status, checkpoint, *, media_id=None, error_code=None):
        self.updates.append((status, media_id, error_code))
        self.job["status"] = status
        if status == "publishing" and self.fail_permit_once:
            self.fail_permit_once = False
            raise TimeoutError("SECRET_TRANSPORT_MESSAGE")
        if status == "completed" and self.fail_ack_once:
            self.fail_ack_once = False
            raise TimeoutError("SECRET_TRANSPORT_MESSAGE")
        return {"ok": True, "job": deepcopy(self.job)}

    def download(self, job, directory):
        self.downloads += 1
        path = Path(directory) / "story.png"
        path.write_bytes(self.content)
        return path


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", "1")
    monkeypatch.setenv("QRSTACK_TEST_ACCOUNTS", "internal")
    key = Fernet.generate_key()
    vault = Vault(tmp_path / "v.db", key)
    vault.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}, "user_id": "42"})
    instagram = Mock(user_id=42)
    instagram.account_info.return_value = {"user": {"username": "internal", "pk": 42}}
    instagram.get_settings.return_value = {"cookies": {}}
    instagram.photo_upload_to_story.return_value = "story-123"
    factory = Mock(return_value=instagram)
    publisher = Publisher(vault, factory)
    api = FakePlatform()
    runner = PlatformRunner(api, publisher, {"demo": "internal"})
    yield runner, api, vault, instagram, factory, key
    vault.close()


def test_platform_publishes_one_story_and_persists_ack(bridge):
    runner, api, vault, instagram, factory, _ = bridge
    assert runner.run_once() == {"status": "completed", "job_id": "job-1", "media_id": "story-123"}
    assert [row[0] for row in api.updates] == ["preparing", "publishing", "completed"]
    instagram.photo_upload_to_story.assert_called_once()
    instagram.login.assert_not_called()
    assert factory.call_count == 1
    assert not vault.platform_deliveries(api.scope)
    assert vault.get_job("internal", runner.local_id(api.job))["status"] == "PUBLISHED"
    assert b"SECRET_CLAIM_TOKEN" not in vault.path.read_bytes()


@pytest.mark.parametrize("field,value", [
    ("restaurant_slug", "other"), ("instagram_username", "customer"),
    ("instagram_user_id", "99"), ("instagram_user_id", ""),
])
def test_platform_rejects_other_tenant_or_immutable_identity(bridge, field, value):
    runner, api, _, instagram, factory, _ = bridge
    api.job[field] = value
    assert runner.run_once()["status"] == "failed_attention"
    assert api.updates == [("failed_attention", None, "local_binding_rejected")]
    assert api.downloads == 0
    factory.assert_not_called()
    instagram.login.assert_not_called()


def test_actual_session_identity_must_match_job(bridge):
    runner, api, vault, instagram, _, _ = bridge
    instagram.account_info.return_value = {"user": {"username": "internal", "pk": 99}}
    instagram.user_id = 99
    assert runner.run_once()["status"] == "outcome_unknown"
    instagram.photo_upload_to_story.assert_not_called()
    assert vault.get("internal")["state"] == "FROZEN"
    assert api.updates[-1][0] == "outcome_unknown"


def test_ack_lost_then_restart_only_retries_ack_even_when_disabled(bridge):
    runner, api, vault, instagram, factory, key = bridge
    api.fail_ack_once = True
    with pytest.raises(TimeoutError):
        runner.run_once()
    path = vault.path
    vault.close()
    restarted = Vault(path, key)
    try:
        api.enabled = False
        # Mapping and global gate can be removed without losing a confirmed ACK.
        reloaded = PlatformRunner(api, Publisher(restarted, factory), {})
        assert reloaded.run_once()["status"] == "completed"
        assert api.next_calls == 1 and api.downloads == 1
        assert [row[0] for row in api.updates].count("publishing") == 1
        assert [row[0] for row in api.updates].count("completed") == 2
        instagram.photo_upload_to_story.assert_called_once()
        factory.assert_called_once()
    finally:
        restarted.close()


@pytest.mark.parametrize("state", ["PENDING", "UNKNOWN"])
def test_unresolved_local_job_after_restart_never_republishes(bridge, state):
    runner, api, vault, instagram, factory, _ = bridge
    local_id = runner.local_id(api.job)
    vault.reserve("internal", local_id)
    if state == "UNKNOWN":
        vault.finish("internal", local_id, "UNKNOWN")
    api.job["status"] = "publishing"
    assert runner.run_once()["status"] == "outcome_unknown"
    assert vault.get("internal")["state"] == "FROZEN"
    assert api.downloads == 0
    factory.assert_not_called()
    instagram.photo_upload_to_story.assert_not_called()


def test_remote_publishing_without_local_record_is_uncertain(bridge):
    runner, api, vault, _, factory, _ = bridge
    api.job["status"] = "publishing"
    assert runner.run_once()["status"] == "outcome_unknown"
    assert api.updates == [("outcome_unknown", None, "runner_interrupted")]
    assert vault.get("internal")["state"] == "FROZEN"
    factory.assert_not_called()


def test_lost_one_time_permit_never_retries_publish(bridge):
    runner, api, vault, _, factory, _ = bridge
    api.fail_permit_once = True
    assert runner.run_once()["status"] == "outcome_unknown"
    assert [row[0] for row in api.updates] == ["preparing", "publishing", "outcome_unknown"]
    assert vault.get("internal")["state"] == "FROZEN"
    factory.assert_not_called()


def test_platform_stop_between_identity_and_upload(bridge):
    runner, api, vault, instagram, _, _ = bridge
    def account_info():
        api.enabled = False
        return {"user": {"username": "internal", "pk": 42}}
    instagram.account_info.side_effect = account_info
    assert runner.run_once()["status"] == "outcome_unknown"
    instagram.photo_upload_to_story.assert_not_called()
    assert vault.get_job("internal", runner.local_id(api.job))["status"] == "UNKNOWN"


def test_platform_job_mutation_during_identity_stops_upload(bridge):
    runner, api, _, instagram, _, _ = bridge
    def account_info():
        api.job["story_link"] = "https://other.example/"
        return {"user": {"username": "internal", "pk": 42}}
    instagram.account_info.side_effect = account_info
    assert runner.run_once()["status"] == "outcome_unknown"
    instagram.photo_upload_to_story.assert_not_called()


def test_unknown_other_job_blocks_before_permit(bridge):
    runner, api, vault, _, factory, _ = bridge
    vault.reserve("internal", "previous-attempt")
    assert runner.run_once()["status"] == "failed_attention"
    assert [row[0] for row in api.updates] == ["failed_attention"]
    factory.assert_not_called()


def test_same_job_id_cannot_start_again(bridge):
    runner, api, _, instagram, _, _ = bridge
    runner.run_once()
    api.job["status"] = "claimed"
    with pytest.raises(PlatformStopped):
        runner.run_once()
    instagram.photo_upload_to_story.assert_called_once()
    assert api.downloads == 1


def test_failed_local_session_save_acks_confirmed_story(bridge):
    runner, _, vault, instagram, _, _ = bridge
    instagram.get_settings.side_effect = RuntimeError("SECRET")
    assert runner.run_once()["status"] == "completed"
    assert vault.get("internal")["state"] == "FROZEN"
    instagram.photo_upload_to_story.assert_called_once()


def response(content, status=200, headers=None):
    value = Mock(status_code=status, headers=headers or {})
    value.iter_content.return_value = [content]
    return value


def test_platform_client_contract_and_no_redirects():
    session = Mock()
    session.request.return_value = response(b'{"ok":true,"job":null}')
    api = PlatformClient("https://worker.example/api", "windows-1", "SECRET", session=session)
    job = {"id": "job1", "claim_token": "CLAIM"}
    api.inspect(job)
    call = session.request.call_args
    assert call.args == ("GET", "https://worker.example/api")
    assert call.kwargs["params"] == {"action": "getInstagramPublisherJob", "publisher_id": "windows-1", "job_id": "job1"}
    assert call.kwargs["headers"]["Authorization"] == "Bearer SECRET"
    assert call.kwargs["headers"]["X-Claim-Token"] == "CLAIM"
    assert call.kwargs["allow_redirects"] is False and call.kwargs["timeout"] == (5, 30)
    assert session.trust_env is False
    api.update(job, "completed", "done", media_id="123")
    assert session.request.call_args.kwargs["json"] == {
        "publisher_id": "windows-1", "job_id": "job1", "claim_token": "CLAIM",
        "status": "completed", "checkpoint": "done", "media_id": "123",
    }


@pytest.mark.parametrize("url", [
    "https://attacker.example/media", "http://worker.example/media",
    "https://worker.example:8443/media", "https://user:password@worker.example/media",
])
def test_media_never_sends_credentials_to_another_origin(tmp_path, url):
    session = Mock()
    api = PlatformClient("https://worker.example/", "windows-1", "SECRET", session=session)
    with pytest.raises(PlatformStopped):
        api.download({"media_url": url, "claim_token": "CLAIM"}, tmp_path)
    session.request.assert_not_called()


def test_media_redirect_is_rejected_without_second_request(tmp_path):
    session = Mock()
    session.request.return_value = response(b"", status=302, headers={"Location": "https://attacker.example/"})
    api = PlatformClient("https://worker.example/", "windows-1", "SECRET", session=session)
    with pytest.raises(PlatformStopped):
        api.download({"media_url": "https://worker.example/media", "claim_token": "CLAIM"}, tmp_path)
    session.request.assert_called_once()


@pytest.mark.parametrize("problem", ["hash", "dimensions", "too_large", "content_length"])
def test_media_hash_dimensions_and_size_limits(tmp_path, problem):
    content = image_bytes((10, 10) if problem == "dimensions" else (1080, 1920))
    headers = {}
    if problem == "too_large":
        content = b"x" * (MAX_MEDIA_BYTES + 1)
    if problem == "content_length":
        headers["Content-Length"] = str(MAX_MEDIA_BYTES + 1)
    session = Mock()
    session.request.return_value = response(content, headers=headers)
    api = PlatformClient("https://worker.example/", "windows-1", "SECRET", session=session)
    job = {"media_url": "https://worker.example/media", "claim_token": "CLAIM",
           "media_sha256": "0" * 64 if problem == "hash" else hashlib.sha256(content).hexdigest()}
    with pytest.raises(PlatformStopped):
        api.download(job, tmp_path)
    session.request.return_value.close.assert_called_once()


def test_media_download_validates_content_and_chooses_real_extension(tmp_path):
    content = image_bytes()
    session = Mock()
    session.request.return_value = response(content)
    api = PlatformClient("https://worker.example/", "windows-1", "SECRET", session=session)
    path = api.download({"media_url": "https://worker.example/media", "claim_token": "CLAIM",
                         "media_sha256": hashlib.sha256(content).hexdigest()}, tmp_path)
    assert path.suffix == ".png" and path.read_bytes() == content


@pytest.mark.parametrize("value", ['{}', '{"demo":"same","another":"same"}', '{"demo":42}', '[]'])
def test_account_map_requires_explicit_isolated_accounts(value):
    with pytest.raises(PlatformStopped):
        load_account_map(value)


def test_runner_process_lock_rejects_concurrent_execution(tmp_path):
    with runner_lock(tmp_path / "vault.db"):
        with pytest.raises(PlatformStopped):
            with runner_lock(tmp_path / "vault.db"):
                pytest.fail("Concurrent runner unexpectedly acquired lock")


@pytest.mark.parametrize("permission", [False, True])
def test_crashed_journal_never_starts_instagram(bridge, permission):
    runner, api, vault, instagram, factory, _ = bridge
    delivery = {"id": runner.local_id(api.job), "scope": api.scope, "account": "internal",
                "job": deepcopy(api.job), "acked": False, "permission_requested": permission}
    vault.save_platform_delivery(delivery, new=True)
    assert runner.run_once()["status"] == ("outcome_unknown" if permission else "failed_attention")
    assert api.next_calls == 0 and api.downloads == 0
    factory.assert_not_called()
    instagram.login.assert_not_called()


def test_binding_rejection_ack_loss_is_recoverable_without_account(bridge):
    runner, api, vault, _, factory, _ = bridge
    api.job["restaurant_slug"] = "unmapped"
    update = api.update
    def lost_ack(*args, **kwargs):
        update(*args, **kwargs)
        raise TimeoutError("SECRET")
    api.update = lost_ack
    with pytest.raises(TimeoutError):
        runner.run_once()
    assert vault.platform_deliveries(api.scope)[0]["account"] is None
    api.update = update
    assert runner.run_once()["status"] == "failed_attention"
    assert api.next_calls == 1
    factory.assert_not_called()


def test_backend_guard_is_installed_for_every_upload_step(bridge):
    runner, api, vault, instagram, _, _ = bridge
    def upload(*args, before_request, **kwargs):
        before_request()
        api.enabled = False
        before_request()
        pytest.fail("Guard failed to stop disabled backend")
    instagram.photo_upload_to_story.side_effect = upload
    assert runner.run_once()["status"] == "outcome_unknown"
    assert vault.get_job("internal", runner.local_id(api.job))["status"] == "UNKNOWN"


def test_idle_honors_server_poll_delay(bridge):
    runner, api, _, _, factory, _ = bridge
    api.next_job = lambda: {"ok": True, "job": None, "poll_after_seconds": 120}
    assert runner.run_once() == {"status": "idle", "poll_after_seconds": 120}
    factory.assert_not_called()


def test_local_cooldown_blocks_before_download_or_publishing_permit(bridge):
    runner, api, vault, _, factory, _ = bridge
    vault.reserve("internal", "earlier-direct-publication")
    vault.finish("internal", "earlier-direct-publication", "PUBLISHED", "confirmed-story")
    assert runner.run_once()["status"] == "failed_attention"
    assert api.updates == [("failed_attention", None, "local_publication_cooldown")]
    assert api.downloads == 0
    factory.assert_not_called()


def test_transient_completed_ack_retry_loop_never_republishes(bridge):
    from qrstack_instagram.platform import PlatformUnavailable, run_loop
    runner, api, _, instagram, factory, _ = bridge
    original_update = api.update
    failed = [False]
    def update(*args, **kwargs):
        result = original_update(*args, **kwargs)
        if args[1] == "completed" and not failed[0]:
            failed[0] = True
            raise PlatformUnavailable(75)
        return result
    api.update = update
    original_run = runner.run_once
    executions = [0]
    def run():
        executions[0] += 1
        if executions[0] == 3:
            return {"status": "failed_attention"}
        return original_run()
    runner.run_once = run
    sleeps = []
    run_loop(runner, loop=True, sleep=sleeps.append, emit=Mock())
    assert sleeps == [75, 30]
    assert [row[0] for row in api.updates].count("publishing") == 1
    assert [row[0] for row in api.updates].count("completed") == 2
    instagram.photo_upload_to_story.assert_called_once()
    factory.assert_called_once()


def test_default_port_host_case_and_path_do_not_reset_local_job_namespace():
    first = PlatformClient("https://worker.example/", "windows-1", "SECRET", session=Mock())
    second = PlatformClient("https://WORKER.example:443/api", "windows-1", "ROTATED", session=Mock())
    assert first.scope == second.scope


def test_cli_local_check_never_uses_network(bridge, monkeypatch, capsys):
    _, _, vault, _, _, key = bridge
    for name, value in {
        "QRSTACK_PLATFORM_URL": "https://worker.example/", "QRSTACK_PUBLISHER_ID": "windows-1",
        "QRSTACK_PUBLISHER_TOKEN": "SECRET_TOKEN", "QRSTACK_PLATFORM_ACCOUNT_MAP": '{"demo":"internal"}',
        "QRSTACK_VAULT_KEY": key.decode(), "QRSTACK_VAULT_PATH": str(vault.path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.argv", ["qrstack-instagram-platform", "check"])
    main()
    output = capsys.readouterr().out
    assert json.loads(output)["source"] == "LOCAL"
    assert "SECRET" not in output


def test_cli_does_not_log_secret_or_upstream_traceback(monkeypatch, capsys):
    monkeypatch.setenv("QRSTACK_PLATFORM_URL", "https://SECRET@worker.example/")
    monkeypatch.setenv("QRSTACK_PUBLISHER_ID", "windows-1")
    monkeypatch.setenv("QRSTACK_PUBLISHER_TOKEN", "SECRET_TOKEN")
    monkeypatch.setattr("sys.argv", ["qrstack-instagram-platform", "run", "--once"])
    with pytest.raises(SystemExit) as stopped:
        main()
    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert "SECRET" not in output.out + output.err
    assert "Traceback" not in output.out + output.err
