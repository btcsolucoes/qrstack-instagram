import json
from io import BytesIO
from unittest.mock import Mock

import pytest
import requests
from cryptography.fernet import Fernet
from curl_cffi import Curl
from curl_cffi import requests as curl_requests
from PIL import Image

from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from qrstack_instagram import Publisher, PublicationStopped, Vault
from qrstack_instagram.vault import AccountChanged


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", "1")
    monkeypatch.setenv("QRSTACK_TEST_ACCOUNTS", "internal")
    key = Fernet.generate_key()
    vault = Vault(tmp_path / "v.db", key)
    vault.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}, "user_id": "42"})
    client = Mock(user_id=42)
    client.account_info.return_value = {"user": {"pk": 42, "username": "internal"}}
    client.get_settings.return_value = {"cookies": {}}
    client.photo_upload_to_story.return_value = "123"
    factory = Mock(return_value=client)
    image = tmp_path / "story.png"
    Image.new("RGB", (1080, 1920)).save(image)
    yield Publisher(vault, factory), vault, client, factory, image, key
    vault.close()


@pytest.mark.parametrize("response,user_id", [
    ({"user": {"pk": 42, "username": "customer"}}, 42),
    ({"user": {"pk": 99, "username": "internal"}}, 99),
    ({"user": {"pk": 42, "username": "internal"}}, 99),
    ({"status": "ok"}, 42),
])
@pytest.mark.parametrize("action", ["connect", "publish", "verify"])
def test_identity_mismatch_never_uploads(harness, response, user_id, action):
    publisher, vault, client, _, image, _ = harness
    client.account_info.return_value = response
    client.user_id = user_id
    with pytest.raises(PublicationStopped):
        if action == "connect":
            publisher.connect("internal", "secret")
        elif action == "publish":
            publisher.publish("internal", "one", image, "https://example.com")
        else:
            publisher.verify("internal")
    client.photo_upload_to_story.assert_not_called()
    assert vault.get("internal")["state"] == "FROZEN"
    client.close.assert_called_once()


@pytest.mark.parametrize("action", ["connect", "publish"])
def test_stop_from_another_process_during_identity_check(harness, action):
    publisher, vault, client, _, image, key = harness
    other = Vault(image.parent / "v.db", key)
    def stop():
        Publisher(other).disable_account("internal")
        return {"user": {"pk": 42, "username": "internal"}}
    client.account_info.side_effect = stop
    try:
        with pytest.raises(PublicationStopped):
            if action == "connect":
                publisher.connect("internal", "secret")
            else:
                publisher.publish("internal", "one", image, "https://example.com")
        client.photo_upload_to_story.assert_not_called()
        assert vault.get("internal")["state"] == "FROZEN"
    finally:
        other.close()


def test_stop_after_server_acceptance_preserves_published_job(harness):
    publisher, vault, client, _, image, _ = harness
    def upload(*args, **kwargs):
        publisher.disable_account("internal")
        return "123"
    client.photo_upload_to_story.side_effect = upload
    assert publisher.publish("internal", "one", image, "https://example.com") == "123"
    assert vault.get("internal")["state"] == "FROZEN"
    assert vault.get_job("internal", "one") == {"status": "PUBLISHED", "story": "123"}


def test_session_save_failure_preserves_confirmed_story(harness):
    publisher, vault, client, _, image, _ = harness
    client.get_settings.side_effect = RuntimeError("private data")
    with pytest.raises(PublicationStopped):
        publisher.publish("internal", "one", image, "https://example.com")
    assert vault.get_job("internal", "one") == {"status": "PUBLISHED", "story": "123"}
    assert vault.get("internal")["state"] == "FROZEN"
    client.photo_upload_to_story.assert_called_once()


def test_stop_between_upload_and_configuration(harness):
    publisher, vault, _, factory, image, _ = harness
    client = Client({"cookies": {"ds_user_id": "42"}})
    client.account_info = Mock(return_value={"user": {"pk": 42, "username": "internal"}})
    client.private_request = Mock(return_value={"status": "ok"})
    def upload(*args, **kwargs):
        publisher.disable_account("internal")
        return "upload", 1080, 1920
    client.photo_rupload = Mock(side_effect=upload)
    factory.return_value = client
    with pytest.raises(PublicationStopped):
        publisher.publish("internal", "one", image, "https://example.com")
    assert [call.args[0] for call in client.private_request.call_args_list] == ["media/validate_reel_url/"]
    assert vault.get("internal")["state"] == "FROZEN"
    assert vault.get_job("internal", "one")["status"] == "UNKNOWN"


def test_invalid_image_does_not_reserve_or_connect(harness):
    publisher, vault, _, factory, image, _ = harness
    image.write_bytes(b"not an image")
    with pytest.raises(OSError):
        publisher.publish("internal", "one", image, "https://example.com")
    factory.assert_not_called()
    assert vault.get_job("internal", "one") is None


def test_unresolved_job_blocks_reconnection_and_reservation(harness):
    publisher, vault, _, factory, _, _ = harness
    vault.reserve("internal", "one")
    vault.finish("internal", "one", "UNKNOWN")
    with pytest.raises(PublicationStopped):
        publisher.connect("internal", "secret", fresh_login=True)
    with pytest.raises(ValueError):
        vault.reserve("internal", "two")
    factory.assert_not_called()


def test_compare_and_swap_and_old_vault_payload(harness):
    _, vault, _, _, _, _ = harness
    old = {"account": "legacy", "state": "HEALTHY", "settings": {"cookies": {}}}
    encrypted = vault.cipher.encrypt(json.dumps(old).encode())
    vault.db.execute("INSERT INTO accounts VALUES (?,?)", ("legacy", encrypted))
    vault.db.commit()
    saved = vault.put("legacy", {"state": "FROZEN"}, expected_revision=0, merge=True)
    assert saved["revision"] == 1 and saved["settings"] == old["settings"]
    with pytest.raises(AccountChanged):
        vault.put("legacy", old, expected_revision=0)
    assert vault.get("legacy")["state"] == "FROZEN"


def test_verify_is_read_only_and_does_not_resume_account(harness):
    publisher, vault, client, _, _, _ = harness
    vault.reserve("internal", "one")
    vault.finish("internal", "one", "PUBLISHED", "123")
    publisher.disable_account("internal")
    client.get_story_status.return_value = {"pk": "123"}
    result = publisher.verify(" @Internal ", "one")
    assert result == {"account": "internal", "session": "VALID", "state": "FROZEN",
                      "job": "one", "publication": "PUBLISHED", "story": "PRESENT"}
    client.get_story_status.assert_called_once_with("123")
    client.login.assert_not_called()
    client.photo_upload_to_story.assert_not_called()
    assert vault.get("internal")["state"] == "FROZEN"


def test_verify_missing_story_is_not_a_retry(harness):
    publisher, vault, client, _, _, _ = harness
    vault.reserve("internal", "one")
    vault.finish("internal", "one", "PUBLISHED", "123")
    client.get_story_status.return_value = None
    assert publisher.verify("internal", "one")["story"] == "NOT_FOUND"
    assert vault.get_job("internal", "one")["status"] == "PUBLISHED"
    client.photo_upload_to_story.assert_not_called()


def test_expired_session_has_no_automatic_login():
    client = Client({"cookies": {"ds_user_id": "42", "sessionid": "expired"}})
    try:
        client.account_info = Mock(side_effect=LoginRequired())
        client.bloks_caa_login = Mock()
        with pytest.raises(LoginRequired):
            client.login("internal", "secret")
        client.bloks_caa_login.assert_not_called()
        saved_uuid = client.uuid
        client.clear_login_session()
        assert client.user_id is None
        assert "Authorization" not in client.private.headers
        assert client.uuid == saved_uuid
        client.bloks_caa_login.return_value = {"logged_in": True}
        assert client.login("internal", "secret") is True
        client.bloks_caa_login.assert_called_once()
        assert client.password is None
        assert "password" not in client.get_settings()
    finally:
        client.close()


@pytest.mark.parametrize("extension", ["png", "webp", "jpg"])
def test_real_encoded_upload_has_jpeg_type_and_timeout(tmp_path, extension):
    path = tmp_path / f"story.{extension}"
    Image.new("RGB", (1080, 1920)).save(path)
    client = Client()
    try:
        client.private.post = Mock(return_value=Mock(status_code=200))
        _, width, height = client.photo_rupload(path, for_story=True, resize_mode="fit")
        call = client.private.post.call_args
        with Image.open(BytesIO(call.kwargs["data"])) as decoded:
            assert decoded.format == "JPEG" and decoded.size == (width, height) == (1080, 1920)
        assert call.kwargs["headers"]["X-Entity-Type"] == "image/jpeg"
        assert call.kwargs["timeout"] == 25
        assert call.kwargs["allow_redirects"] is False
        client.private.post.assert_called_once()
    finally:
        client.close()


def test_offline_guard_covers_requests_and_native_curl():
    with requests.Session() as session:
        with pytest.raises(AssertionError, match="Offline tests"):
            session.get("https://example.invalid")
    with curl_requests.Session() as session:
        with pytest.raises(AssertionError, match="Offline tests"):
            session.get("https://example.invalid")
    curl = Curl()
    try:
        with pytest.raises(AssertionError, match="Offline tests"):
            curl.perform()
    finally:
        curl.close()


@pytest.mark.parametrize("endpoint", ["private", "graphql"])
def test_private_request_has_timeout_and_never_repeats_post(monkeypatch, endpoint):
    client = Client()
    try:
        monkeypatch.setattr("instagrapi.mixins.private.time.sleep", lambda _: None)
        client.private.post = Mock(side_effect=requests.exceptions.Timeout("sensitive response"))
        with pytest.raises(requests.exceptions.Timeout):
            if endpoint == "private":
                client.private_request("media/configure_to_story/", {"upload_id": "one"})
            else:
                client.private_graphql_www_request("IGUSDIDRegistrationMutation", {})
        client.private.post.assert_called_once()
        assert client.private.post.call_args.kwargs["timeout"] == 25
        assert client.private.post.call_args.kwargs["allow_redirects"] is False
    finally:
        client.close()


def test_password_key_fetch_has_timeout():
    client = Client()
    try:
        client.public.get = Mock(return_value=Mock(headers={
            "ig-set-password-encryption-key-id": "1", "ig-set-password-encryption-pub-key": "key"}))
        assert client.password_publickeys() == (1, "key")
        assert client.public.get.call_args.kwargs["timeout"] == 25
    finally:
        client.close()


def test_connect_pins_verified_identity(harness):
    publisher, vault, client, _, _, _ = harness
    publisher.connect(" @Internal ", "secret", fresh_login=True)
    client.clear_login_session.assert_called_once()
    client.login.assert_called_once_with("internal", "secret")
    assert vault.get("internal")["user_id"] == "42"
    assert "secret" not in json.dumps(vault.get("internal"))


def test_cli_rejects_disabled_publisher_before_password(harness, monkeypatch):
    from qrstack_instagram.cli import main
    _, _, _, _, image, key = harness
    monkeypatch.setenv("QRSTACK_VAULT_KEY", key.decode())
    monkeypatch.setenv("QRSTACK_VAULT_PATH", str(image.parent / "v.db"))
    monkeypatch.delenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER")
    monkeypatch.setattr("sys.argv", ["qrstack-instagram", "connect", "internal"])
    password = Mock()
    monkeypatch.setattr("getpass.getpass", password)
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    password.assert_not_called()


def test_cli_invalid_key_has_no_traceback_or_secret(monkeypatch, capsys):
    from qrstack_instagram.cli import main
    monkeypatch.setenv("QRSTACK_VAULT_KEY", "SECRET_INVALID_KEY")
    monkeypatch.setattr("sys.argv", ["qrstack-instagram", "status", "internal"])
    with pytest.raises(SystemExit):
        main()
    output = capsys.readouterr()
    assert "SECRET_INVALID_KEY" not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert "STOPPED:" in output.out
