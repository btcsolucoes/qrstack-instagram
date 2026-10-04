import json
import socket
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet, InvalidToken
from instagrapi import Client, StoryLink
from instagrapi.exceptions import ChallengeRequired, ClientRequestTimeout
from qrstack_instagram import Vault, Publisher, PublicationStopped


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must never contact Instagram")
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_client_session_isolation():
    a, b = Client(), Client()
    a.private.cookies.set("sessionid", "secret")
    assert "sessionid" not in b.private.cookies
    restored = Client(a.get_settings())
    assert restored.uuid == a.uuid
    assert restored.session_retry_total == 0
    for c in (a, b, restored):
        assert not hasattr(c, "direct_send")
        assert not hasattr(c, "user_follow")
        assert not hasattr(c, "media_like")
        assert not hasattr(c, "photo_upload")
        assert not hasattr(c, "challenge_resolve")
        c.close()


@pytest.mark.parametrize("error", [ChallengeRequired, ClientRequestTimeout])
def test_no_transport_retry_or_challenge(error):
    c = Client()
    c._send_private_request = Mock(side_effect=error())
    with pytest.raises(error):
        c.private_request("accounts/current_user/")
    assert c._send_private_request.call_count == 1
    c.close()


def test_link_payload_single_configuration(tmp_path):
    c = Client()
    c.photo_rupload = Mock(return_value=("upload", 1080, 1920))
    c.private_request = Mock(side_effect=[{"status": "ok"}, {"media": {"pk": "123"}}])
    assert c.photo_upload_to_story(tmp_path / "story.jpg", [StoryLink(webUri="https://example.com/menu", x=.5, y=.72, width=.56, height=.1)]) == "123"
    data = c.private_request.call_args.args[1]
    sticker = json.loads(data["tap_models"])[0]
    assert sticker["type"] == "story_link"
    assert sticker["url"] == "https://example.com/menu"
    assert sticker["x"] == .5 and sticker["y"] == .72
    assert c.private_request.call_count == 2
    c.close()


def test_encryption_and_tenant_binding(tmp_path):
    path = tmp_path / "vault.db"
    key = Fernet.generate_key()
    v = Vault(path, key)
    v.put("a", {"settings": {"cookies": {"sessionid": "SECRET_COOKIE"}}})
    assert b"SECRET_COOKIE" not in path.read_bytes()
    assert v.get("b")["settings"] is None
    with pytest.raises(InvalidToken):
        Vault(path, Fernet.generate_key()).get("a")
    payload = v.db.execute("SELECT payload FROM accounts WHERE id='a'").fetchone()[0]
    v.db.execute("INSERT INTO accounts VALUES ('b',?)", (payload,))
    v.db.commit()
    with pytest.raises(ValueError):
        v.get("b")
    v.close()


def test_freeze_idempotency_and_no_secret_logging(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", "1")
    monkeypatch.setenv("QRSTACK_TEST_ACCOUNTS", "internal")
    v = Vault(tmp_path / "v.db", Fernet.generate_key())
    v.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}})
    fake = Mock()
    fake.photo_upload_to_story.side_effect = ChallengeRequired("SECRET_COOKIE")
    factory = Mock(return_value=fake)
    p = Publisher(v, factory)
    image = tmp_path / "story.jpg"
    image.write_bytes(b"fixture")
    with pytest.raises(PublicationStopped):
        p.publish("internal", "job1", str(image), "https://example.com")
    assert v.get("internal")["state"] == "FROZEN"
    assert "SECRET_COOKIE" not in caplog.text
    with pytest.raises(PublicationStopped):
        p.publish("internal", "job2", str(image), "https://example.com")
    assert factory.call_count == 1
    v.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}})
    with pytest.raises(ValueError):
        p.publish("internal", "job2", str(image), "https://example.com")
    v.close()


def test_kill_switch_blocks_before_client(tmp_path, monkeypatch):
    monkeypatch.delenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", raising=False)
    v = Vault(tmp_path / "v.db", Fernet.generate_key())
    factory = Mock()
    with pytest.raises(PublicationStopped):
        Publisher(v, factory).connect("internal", "secret")
    factory.assert_not_called()
    v.close()


def test_success_survives_restart_and_prevents_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("QRSTACK_ENABLE_PRIVATE_PUBLISHER", "1")
    monkeypatch.setenv("QRSTACK_TEST_ACCOUNTS", "internal")
    key = Fernet.generate_key()
    path = tmp_path / "v.db"
    v = Vault(path, key)
    v.put("internal", {"state": "HEALTHY", "settings": {"cookies": {}}})
    fake = Mock()
    fake.photo_upload_to_story.return_value = "123"
    fake.get_settings.return_value = {"cookies": {}}
    image = tmp_path / "story.jpg"
    image.write_bytes(b"fixture")
    assert Publisher(v, lambda **kw: fake).publish("internal", "one", str(image), "https://example.com") == "123"
    v.close()
    v = Vault(path, key)
    with pytest.raises(ValueError):
        v.reserve("internal", "one")
    assert fake.photo_upload_to_story.call_count == 1
    v.close()
