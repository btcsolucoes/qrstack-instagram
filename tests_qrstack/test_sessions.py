from copy import deepcopy
from unittest.mock import Mock
import json
import pytest
from cryptography.fernet import Fernet
from instagrapi.exceptions import ChallengeRequired
from qrstack_instagram import Publisher, Vault
from qrstack_instagram.sessions import SessionBridge
from qrstack_instagram.platform import PlatformUnavailable


@pytest.fixture
def session_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('QRSTACK_ENABLE_PRIVATE_PUBLISHER', '1')
    monkeypatch.setenv('QRSTACK_TEST_ACCOUNTS', '')
    vault = Vault(tmp_path / 'vault.db', Fernet.generate_key())
    client = Mock(user_id=42)
    client.account_info.return_value = {'user': {'username': 'internal', 'pk': 42}}
    client.get_settings.return_value = {'cookies': {'sessionid': 'SECRET_SESSION'}}
    publisher = Publisher(vault, Mock(return_value=client))
    api = Mock(scope='scope')
    queue = [{'id': 'connection1', 'restaurant_slug': 'demo', 'instagram_username': 'internal',
              'instagram_user_id': '42', 'claim_token': 'SECRET_CLAIM', 'password': 'FAKE_PASSWORD'}]
    def call(action, **kwargs):
        if action == 'claimInstagramConnection':
            return {'connection': deepcopy(queue.pop()) if queue else None}
        return {'ok': True}
    api.call.side_effect = call
    bridge = SessionBridge(api, publisher, {})
    yield bridge, client, vault, api, queue
    vault.close()


def test_connects_once_erases_password_and_restores_approved_mapping(session_bridge):
    bridge, client, vault, api, _ = session_bridge
    assert bridge.run_once()['status'] == 'session_connected'
    client.login.assert_called_once_with('internal', 'FAKE_PASSWORD')
    client.photo_upload_to_story.assert_not_called()
    record = vault.session_connections('scope')[0]
    assert 'password' not in record
    assert record['acked'] is True
    assert 'FAKE_PASSWORD' not in json.dumps(vault.get('internal'))
    assert all(secret not in vault.path.read_bytes() for secret in (b'FAKE_PASSWORD', b'SECRET_CLAIM', b'SECRET_SESSION'))
    restored = SessionBridge(api, Publisher(vault), {})
    assert restored.accounts == {'demo': 'internal'}
    restored.publisher.gate('internal')
    assert bridge.run_once() is None
    assert client.login.call_count == 1


def test_lost_ack_retries_only_result_not_login(session_bridge):
    bridge, client, vault, api, _ = session_bridge
    original = api.call.side_effect
    def fail_ack(action, **kwargs):
        if action == 'completeInstagramConnection':
            raise PlatformUnavailable()
        return original(action, **kwargs)
    api.call.side_effect = fail_ack
    with pytest.raises(PlatformUnavailable):
        bridge.run_once()
    assert vault.session_connections('scope')[0]['state'] == 'connected'
    api.call.side_effect = original
    assert bridge.run_once()['status'] == 'session_connected'
    assert client.login.call_count == 1


def test_interrupted_login_is_reviewed_without_replay(session_bridge):
    bridge, client, vault, api, queue = session_bridge
    record = queue.pop()
    record.update(scope='scope', acked=False)
    vault.save_session_connection(record, new=True)
    assert bridge.run_once()['status'] == 'session_review_required'
    client.login.assert_not_called()
    assert vault.get('internal')['state'] == 'FROZEN'


def test_challenge_stops_login_and_reports_verification(session_bridge):
    bridge, client, vault, _, _ = session_bridge
    client.login.side_effect = ChallengeRequired('DO_NOT_LOG_ME')
    assert bridge.run_once()['status'] == 'session_verification_required'
    assert vault.get('internal')['state'] == 'FROZEN'
    assert bridge.run_once() is None
    assert client.login.call_count == 1
    assert not bridge.accounts


def test_wrong_immutable_identity_never_becomes_publishable(session_bridge):
    bridge, client, vault, _, queue = session_bridge
    queue[0]['instagram_user_id'] = '99'
    assert bridge.run_once()['status'] == 'session_identity_mismatch'
    assert vault.get('internal')['state'] == 'FROZEN'
    assert not bridge.accounts
    assert not bridge.publisher.approved_accounts


def test_poll_only_reports_cached_session_without_instagram_requests(session_bridge):
    bridge, client, _, _, queue = session_bridge
    queue.clear()
    bridge.accounts['demo'] = 'internal'
    assert bridge.run_once() is None
    bridge.publisher.client_factory.assert_not_called()


def test_unresolved_job_blocks_reconnect(session_bridge):
    bridge, client, vault, _, _ = session_bridge
    vault.reserve('internal', 'unresolved')
    assert bridge.run_once()['status'] == 'session_disconnected'
    client.login.assert_not_called()
