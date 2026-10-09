"""Explicit owner-requested login. Polling only reports the local vault state."""
import re
import time


def session_state(value):
    error = value.get('error_class', '')
    if error == 'AccountIdentityMismatch':
        return 'identity_mismatch'
    if any(part in error for part in ('Challenge', 'Checkpoint', 'TwoFactor')):
        return 'verification_required'
    return {'HEALTHY': 'connected', 'RECONNECT_REQUIRED': 'disconnected',
            'COOLDOWN': 'cooldown', 'SUSPENDED': 'suspended',
            'FROZEN': 'review_required'}.get(value.get('state'), 'review_required')


class SessionBridge:
    def __init__(self, api, publisher, accounts):
        self.api, self.publisher, self.accounts = api, publisher, accounts
        self.vault = publisher.vault
        self.last_report = 0
        for record in self.vault.session_connections(api.scope):
            if record.get('state') == 'connected':
                self._approve(record)

    def _approve(self, record):
        slug, account = record['restaurant_slug'], record['instagram_username']
        if any(key != slug and value == account for key, value in self.accounts.items()):
            raise ValueError('Account already assigned locally')
        self.accounts[slug] = account
        self.publisher.approved_accounts.add(account)

    def _ack(self, record):
        self.api.call('completeInstagramConnection', method='POST', data={
            **{key: record[key] for key in ('id', 'claim_token', 'state')},
            'verified_at': record.get('verified_at')})
        record['acked'] = True
        self.vault.save_session_connection(record)
        return {'status': 'session_' + record['state']}

    def run_once(self):
        # An interrupted login is never replayed. Only its result can be retried.
        for record in self.vault.session_connections(self.api.scope):
            if not record.get('acked'):
                if not record.get('state'):
                    record['state'] = 'review_required'
                    self.publisher.disable_account(record['instagram_username'])
                    self.vault.save_session_connection(record)
                return self._ack(record)
        if time.monotonic() - self.last_report >= 30:
            sessions = []
            for slug, account in self.accounts.items():
                value = self.vault.get(account)
                sessions.append({'restaurant_slug': slug, 'instagram_username': account,
                                 'instagram_user_id': str(value.get('user_id') or ''),
                                 'state': session_state(value), 'verified_at': value.get('verified_at')})
            self.api.call('reportInstagramSessions', method='POST', data={'sessions': sessions})
            self.last_report = time.monotonic()
        response = self.api.call('claimInstagramConnection', method='POST')
        incoming = response.get('connection')
        if incoming is None:
            return None
        password = incoming.pop('password', None)
        record = {key: incoming[key] for key in ('id', 'claim_token', 'restaurant_slug', 'instagram_username', 'instagram_user_id')}
        record.update(scope=self.api.scope, acked=False)
        # Commit before constructing an Instagram client or attempting login.
        self.vault.save_session_connection(record, new=True)
        account = record['instagram_username']
        try:
            if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', record['restaurant_slug'])
                    or not re.fullmatch(r'[a-z0-9._]{1,30}', account)
                    or not re.fullmatch(r'[1-9][0-9]*', record['instagram_user_id'])
                    or not isinstance(password, str) or not 1 <= len(password) <= 128
                    or any(slug != record['restaurant_slug'] and name == account for slug, name in self.accounts.items())):
                record['state'] = 'identity_mismatch'
            else:
                # Authenticated owner request grants this account's explicit authorization.
                self.publisher.approved_accounts.add(account)
                self.publisher.connect(account, password, fresh_login=True,
                                       expected_user_id=record['instagram_user_id'])
                record['state'] = 'connected'
                record['verified_at'] = self.vault.get(account).get('verified_at')
                self._approve(record)
        except Exception:
            record['state'] = session_state(self.vault.get(account))
            if record['state'] == 'connected':
                record['state'] = 'connection_failed'
        finally:
            password = None
            if record.get('state') != 'connected' and account not in self.accounts.values():
                self.publisher.approved_accounts.discard(account)
        self.vault.save_session_connection(record)
        self.last_report = 0
        return self._ack(record)
