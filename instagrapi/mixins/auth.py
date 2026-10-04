import base64
import hashlib
import json
import random
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Union
import requests
from instagrapi import config
from instagrapi.exceptions import BadCredentials, ClientError, LoginRequired, ReloginAttemptExceeded, TwoFactorRequired
from instagrapi.utils.serialization import dumps
TIMELINE_FEED_REASON = Literal['cold_start_fetch', 'warm_start_fetch', 'pagination', 'pull_to_refresh', 'auto_refresh']
REELS_TRAY_REASON = Literal['cold_start', 'pull_to_refresh']

class LoginMixin:
    username = None
    password = None
    authorization_data = {}
    last_login = None
    relogin_attempt = 0
    device_settings = {}
    client_session_id = ''
    tray_session_id = ''
    advertising_id = ''
    android_device_id = ''
    request_id = ''
    phone_id = ''
    app_id = '567067343352427'
    uuid = ''
    mid = ''
    country = 'US'
    country_code = 1
    locale = 'en_US'
    timezone_offset: int = -14400
    timezone_name: str = ''
    push_disabled: bool = True
    public_request_retries_count = 3
    public_request_retries_timeout = 2
    session_retry_total = 3
    session_retry_backoff_factor = 2
    session_retry_statuses = [429, 500, 502, 503, 504]
    ig_u_rur = ''
    ig_www_claim = ''

    def __init__(self):
        self.bloks_versioning_id = config.APP_SETTINGS[config.DEFAULT_APP_VERSION]['bloks_versioning_id']
        self.user_agent = None
        self.settings = None
        self.override_app_version = False

    def _clear_session_state(self, *, clear_private_cookies: bool=False, clear_public_cookies: bool=False, clear_authorization_data: bool=False, clear_authorization_header: bool=False, clear_last_login: bool=False, reset_relogin_attempt: bool=False) -> None:
        if clear_authorization_data:
            self.authorization_data = {}
            self.set_ig_u_rur('')
            self.set_ig_www_claim('')
            for key in list(self.private.headers):
                name = key.lower()
                if name.startswith('ig-u-') or name in ('ig-intended-user-id', 'x-ig-www-claim'):
                    self.private.headers.pop(key, None)
            self.private.headers.update({'IG-INTENDED-USER-ID': '0', 'X-IG-WWW-Claim': '0'})
        if clear_last_login:
            self.last_login = None
        if reset_relogin_attempt:
            self.relogin_attempt = 0
        if clear_authorization_header:
            self.private.headers.pop('Authorization', None)
        if clear_private_cookies:
            self.private.cookies.clear()
        if clear_public_cookies:
            self.public.cookies.clear()

    def _find_login_response_value(self, data: Any, key: str) -> Any:
        if isinstance(data, dict):
            value = data.get(key)
            if value:
                return value
            for child in data.values():
                value = self._find_login_response_value(child, key)
                if value:
                    return value
        elif isinstance(data, list):
            for child in data:
                value = self._find_login_response_value(child, key)
                if value:
                    return value
        return None

    def _extract_two_step_verification_context(self, data: Dict) -> str:
        value = self._find_login_response_value(data, 'two_step_verification_context')
        return value.strip() if isinstance(value, str) else ''

    def _exception_context(self, data: Dict) -> Dict:
        context = deepcopy(data)
        message = context.pop('message', None)
        if message is not None:
            context['instagram_message'] = message
        return context

    def init(self) -> bool:
        """
        Initialize Login helpers

        Returns
        -------
        bool
            A boolean value
        """
        if 'cookies' in self.settings:
            self.private.cookies = requests.utils.cookiejar_from_dict(self.settings['cookies'])
        else:
            self._clear_session_state(clear_private_cookies=True)
        self.authorization_data = self.settings.get('authorization_data', {})
        self.last_login = self.settings.get('last_login')
        timezone_offset = self.settings.get('timezone_offset', self.timezone_offset)
        timezone_name = self.settings.get('timezone_name', self.timezone_name)
        push_disabled = self.settings.get('push_disabled', self.push_disabled)
        locale = self.settings.get('locale', self.locale)
        country = self.settings.get('country', self.country)
        country_code = self.settings.get('country_code', self.country_code)
        self.set_tls_verify(self.settings.get('tls_verify', self.tls_verify))
        self.set_retry_config(request_timeout=self.settings.get('request_timeout', self.request_timeout), public_request_retries_count=self.settings.get('public_request_retries_count', self.public_request_retries_count), public_request_retries_timeout=self.settings.get('public_request_retries_timeout', self.public_request_retries_timeout), session_retry_total=self.settings.get('session_retry_total', self.session_retry_total), session_retry_backoff_factor=self.settings.get('session_retry_backoff_factor', self.session_retry_backoff_factor), session_retry_statuses=self.settings.get('session_retry_statuses', self.session_retry_statuses), public_transport=self.settings.get('public_transport'), private_transport=self.settings.get('private_transport'), public_transport_impersonate=self.settings.get('public_transport_impersonate'))
        self.set_timezone_offset(timezone_offset, timezone_name=timezone_name or None)
        self.set_push_disabled(push_disabled)
        self.set_device(self.settings.get('device_settings'), hydrate_app_profile=True)
        self.set_user_agent(self.settings.get('user_agent'))
        self.set_uuids(self.settings.get('uuids') or {})
        self.set_locale(locale)
        self.set_country(country)
        self.set_country_code(country_code)
        self.mid = self.settings.get('mid', self.cookie_dict.get('mid'))
        self.set_ig_u_rur(self.settings.get('ig_u_rur'))
        self.set_ig_www_claim(self.settings.get('ig_www_claim'))
        self.set_usdid_settings(self.settings.get('usdid'))
        headers = self.base_headers
        if self.authorization:
            headers.update({'Authorization': self.authorization})
        else:
            self.private.headers.pop('Authorization', None)
        if not self.ig_u_rur:
            self.private.headers.pop('IG-U-RUR', None)
        self.private.headers.update(headers)
        return True

    def _caa_result_action_markers(self, outcome: Dict) -> List[str]:
        """Extract CAA step/action markers from the raw Bloks login payload."""
        markers: List[str] = []
        result = outcome.get('result')
        if isinstance(result, dict):
            self._bloks_collect_strings(result, markers)
        return [marker for marker in markers if marker.startswith('CAA_') and ':' in marker]

    def login(self, username: Union[str, None]=None, password: Union[str, None]=None, relogin: bool=False, verification_code: str='') -> bool:
        """Log in using the current Android CAA flow.

        Parameters
        ----------
        username: str
            Instagram Username. Uses stored credentials when omitted.
        password: str
            Instagram Password. Uses stored credentials when omitted.
        relogin: bool
            Clear the current session before logging in, default False.
        verification_code: str
            Two-factor or profile verification code.

        Returns
        -------
        bool
            True after successful login or validation of an existing session.

        Notes
        -----
        Existing sessions are validated before reuse. Rejected sessions are
        cleared and refreshed through CAA. CAA errors propagate directly.
        When Instagram's CAA response directs the client back to the legacy
        accounts flow (``CAA_LOGIN_FALLBACK:...``), this method completes that
        step through ``login_legacy`` so the typed failure reason surfaces;
        use ``login_legacy`` to select the legacy flow directly.
        """
        if username and password:
            self.username = username
            self.password = password
        if self.username is None or self.password is None:
            raise BadCredentials('Both username and password must be provided.')
        if isinstance(self.username, str):
            self.username = self.username.strip()
        if relogin:
            self._clear_session_state(clear_authorization_data=True, clear_authorization_header=True, clear_private_cookies=True, clear_public_cookies=True)
            if self.relogin_attempt > 1:
                raise ReloginAttemptExceeded()
            self.relogin_attempt += 1
        if self.user_id and (not relogin):
            try:
                self.account_info()
            except LoginRequired:
                return self.login(relogin=True, verification_code=verification_code)
            return True
        if not self.bloks_versioning_id:
            raise ClientError('CAA login requires bloks_versioning_id for the saved app profile. Load settings with override_app_version=True to use the supported app profile, or provide the matching Bloks hash.')
        outcome = self.bloks_caa_login(verification_code=verification_code)
        logged = bool(outcome.get('logged_in'))
        if not logged:
            context = self._extract_two_step_verification_context(outcome)
            if context:
                exc = TwoFactorRequired('Instagram returned a Bloks two-factor context from the CAA login flow; provide verification_code for login', response=self.last_response, **self._exception_context(outcome))
                if not verification_code.strip():
                    raise exc
                logged = self._login_with_bloks_two_factor(verification_code, outcome, exc)
            if not logged:
                markers = self._caa_result_action_markers(outcome)
                if any((marker.startswith('CAA_LOGIN_FALLBACK:') for marker in markers)):
                    return self.login_legacy(verification_code=verification_code)
                exc_context = self._exception_context(outcome)
                if markers:
                    exc_context['caa_actions'] = markers
                raise ClientError(str(outcome.get('reason') or 'CAA login did not return a session'), response=self.last_response, **exc_context)
        self.login_flow()
        self.last_login = time.time()
        self.relogin_attempt = 0
        return True

    @property
    def cookie_dict(self) -> dict:
        return self.private.cookies.get_dict()

    @property
    def user_id(self) -> int:
        user_id = self.cookie_dict.get('ds_user_id')
        if not user_id and self.authorization_data:
            user_id = self.authorization_data.get('ds_user_id')
        if user_id:
            return int(user_id)
        return None

    @property
    def device(self) -> dict:
        return {key: val for key, val in self.device_settings.items() if key in ['manufacturer', 'model', 'android_version', 'android_release']}

    def get_settings(self) -> Dict:
        """
        Get current session settings

        Returns
        -------
        Dict
            Current session settings as a Dict
        """
        settings = {'uuids': {'phone_id': self.phone_id, 'uuid': self.uuid, 'client_session_id': self.client_session_id, 'advertising_id': self.advertising_id, 'android_device_id': self.android_device_id, 'request_id': self.request_id, 'tray_session_id': self.tray_session_id}, 'mid': self.mid, 'ig_u_rur': self.ig_u_rur, 'ig_www_claim': self.ig_www_claim, 'authorization_data': self.authorization_data, 'cookies': requests.utils.dict_from_cookiejar(self.private.cookies), 'last_login': self.last_login, 'device_settings': self.device_settings, 'user_agent': self.user_agent, 'country': self.country, 'country_code': self.country_code, 'locale': self.locale, 'timezone_offset': self.timezone_offset, 'timezone_name': self.timezone_name, 'push_disabled': self.push_disabled, 'request_timeout': self.request_timeout, 'public_request_retries_count': self.public_request_retries_count, 'public_request_retries_timeout': self.public_request_retries_timeout, 'session_retry_total': self.session_retry_total, 'session_retry_backoff_factor': self.session_retry_backoff_factor, 'session_retry_statuses': self.session_retry_statuses, 'public_transport': self.public_transport, 'private_transport': self.private_transport, 'public_transport_impersonate': self.public_transport_impersonate, 'tls_verify': self.tls_verify}
        fbns_auth = None
        if getattr(self, 'fbns', None) and getattr(self.fbns, 'auth', None):
            fbns_auth = self.fbns.auth.to_settings()
            if getattr(self, 'settings', None) is not None:
                self.settings['fbns_auth'] = deepcopy(fbns_auth)
        elif getattr(self, 'settings', None) and self.settings.get('fbns_auth') is not None:
            fbns_auth = deepcopy(self.settings['fbns_auth'])
        if fbns_auth:
            settings['fbns_auth'] = fbns_auth
        usdid_settings = self.get_usdid_settings()
        if usdid_settings:
            settings['usdid'] = usdid_settings
        return settings

    def set_settings(self, settings: Dict) -> bool:
        """
        Set session settings

        Returns
        -------
        Bool
        """
        self.settings = deepcopy(settings)
        self.init()
        return True

    def set_tls_verify(self, tls_verify: Union[bool, str]) -> bool:
        self.tls_verify = tls_verify
        for name in ('public', 'private', 'graphql'):
            session = getattr(self, name, None)
            if session is not None:
                session.verify = tls_verify
        if self.settings is not None:
            self.settings['tls_verify'] = tls_verify
        return True

    def set_retry_config(self, request_timeout: Union[int, float, None]=None, public_request_retries_count: int=None, public_request_retries_timeout: Union[int, float]=None, session_retry_total: int=None, session_retry_backoff_factor: Union[int, float]=None, session_retry_statuses: list=None, public_transport: Optional[Literal['requests', 'curl']]=None, public_transport_impersonate: str=None, private_transport: Optional[Literal['requests', 'curl']]=None) -> bool:
        if private_transport is not None:
            private_transport = self._normalize_private_transport(private_transport)
        if request_timeout is not None:
            self.request_timeout = request_timeout
        if public_request_retries_count is not None:
            self.public_request_retries_count = public_request_retries_count
        if public_request_retries_timeout is not None:
            self.public_request_retries_timeout = public_request_retries_timeout
        if session_retry_total is not None:
            self.session_retry_total = session_retry_total
        if session_retry_backoff_factor is not None:
            self.session_retry_backoff_factor = session_retry_backoff_factor
        if session_retry_statuses is not None:
            self.session_retry_statuses = list(session_retry_statuses)
        if public_transport is not None:
            self.public_transport = self._normalize_public_transport(public_transport)
        if public_transport_impersonate is not None:
            self.public_transport_impersonate = public_transport_impersonate
        if public_transport is not None or public_transport_impersonate is not None:
            self.public_user_agent = self._default_public_user_agent(self.public_transport, self.public_transport_impersonate)
            self.public.headers['User-Agent'] = self.public_user_agent
        self._configure_public_session_retry()
        self._configure_private_session_retry(private_transport=private_transport)
        if self.settings is not None:
            self.settings.update({'request_timeout': self.request_timeout, 'public_request_retries_count': self.public_request_retries_count, 'public_request_retries_timeout': self.public_request_retries_timeout, 'session_retry_total': self.session_retry_total, 'session_retry_backoff_factor': self.session_retry_backoff_factor, 'session_retry_statuses': self.session_retry_statuses, 'public_transport': self.public_transport, 'private_transport': self.private_transport, 'public_transport_impersonate': self.public_transport_impersonate})
        return True

    def set_device(self, device: Dict=None, reset: bool=False, hydrate_app_profile: bool=False) -> bool:
        """
        Helper to set a device for login

        Parameters
        ----------
        device: Dict, optional
            Dict of device settings, default is None
        hydrate_app_profile: bool, optional
            Hydrate incomplete app profile fields from the current default app when loading saved settings

        Returns
        -------
        bool
            A boolean value
        """
        device = device or {}
        self.device_settings = dict(config.DEVICE_SETTINGS)
        self.device_settings.update(device)
        seed = None
        if self.settings:
            uuids = self.settings.get('uuids') or {}
            seed = uuids.get('uuid')
        self.set_app(seed=seed, hydrate_incomplete_profile=hydrate_app_profile)
        self.set_user_agent()
        if reset:
            self.set_uuids({})
        return True

    def set_app(self, app: Union[str, Dict]=None, seed: str=None, hydrate_incomplete_profile: bool=False) -> bool:
        """
        Helper to set app version settings

        Parameters
        ----------
        app: Union[str, Dict], optional
            App version string or settings dict
        seed: str, optional
            Seed used for stable app selection
        hydrate_incomplete_profile: bool, optional
            Hydrate incomplete app profile fields from the current default app

        Returns
        -------
        bool
            A boolean value
        """
        app_keys = ('app_version', 'version_code', 'bloks_versioning_id')
        if not getattr(self, 'device_settings', None):
            self.device_settings = dict(config.DEVICE_SETTINGS)
        if not config.APP_SETTINGS:
            raise ValueError('APP_SETTINGS is empty')
        override_app_version = bool(getattr(self, 'override_app_version', False))

        def apply_settings(app_settings: Dict) -> None:
            for key in app_keys:
                val = app_settings.get(key)
                if val:
                    self.device_settings[key] = val

        def pick_by_seed() -> Dict:
            app_values = list(config.APP_SETTINGS.values())
            if seed:
                digest = hashlib.sha256(seed.encode('utf-8')).hexdigest()
                idx = int(digest, 16) % len(app_values)
                return app_values[idx]
            return random.choice(app_values)

        def default_settings() -> Dict:
            return config.APP_SETTINGS.get(config.DEFAULT_APP_VERSION) or pick_by_seed()

        def is_current_or_newer_app_version(value: Any) -> bool:
            try:
                version = tuple((int(part) for part in str(value).split('.')))
                default_version = tuple((int(part) for part in config.DEFAULT_APP_VERSION.split('.')))
            except (TypeError, ValueError):
                return False
            return version >= default_version
        if app:
            if isinstance(app, str):
                matched = config.APP_SETTINGS.get(app)
                if not matched:
                    raise ValueError(f'Unknown app_version: {app}')
                apply_settings(matched)
            else:
                apply_settings(dict(app))
        else:
            app_version = self.device_settings.get('app_version')
            matched = config.APP_SETTINGS.get(app_version) if app_version else None
            if matched and (not override_app_version):
                apply_settings(matched)
            else:
                has_complete_app_profile = all((self.device_settings.get(key) for key in app_keys))
                should_hydrate_incomplete_profile = hydrate_incomplete_profile and (not has_complete_app_profile) and is_current_or_newer_app_version(app_version)
                if override_app_version or not app_version or should_hydrate_incomplete_profile:
                    apply_settings(default_settings())
        if override_app_version:
            self.set_user_agent()
        self.bloks_versioning_id = self.device_settings.get('bloks_versioning_id')
        if self.settings is not None:
            self.settings['device_settings'] = self.device_settings
        return True

    def set_user_agent(self, user_agent: str='', reset: bool=False) -> bool:
        """
        Helper to set user agent

        Parameters
        ----------
        user_agent: str, optional
            User agent, default is ""

        Returns
        -------
        bool
            A boolean value
        """
        data = dict(self.device_settings, locale=self.locale)
        self.user_agent = user_agent or config.USER_AGENT_BASE.format(**data)
        self.settings['user_agent'] = self.user_agent
        if reset:
            self.set_uuids({})
        return True

    def set_uuids(self, uuids: Dict=None) -> bool:
        """
        Helper to set uuids

        Parameters
        ----------
        uuids: Dict, optional
            UUIDs, default is None

        Returns
        -------
        bool
            A boolean value
        """
        previous_phone_id = self.phone_id
        self.phone_id = uuids.get('phone_id', self.generate_uuid())
        self.uuid = uuids.get('uuid', self.generate_uuid())
        self.client_session_id = uuids.get('client_session_id', self.generate_uuid())
        self.advertising_id = uuids.get('advertising_id', self.generate_uuid())
        self.android_device_id = uuids.get('android_device_id', self.generate_android_device_id())
        self.request_id = uuids.get('request_id', self.generate_uuid())
        self.tray_session_id = uuids.get('tray_session_id', self.generate_uuid())
        self.settings['uuids'] = uuids
        if previous_phone_id and previous_phone_id != self.phone_id:
            self.set_usdid_settings({})
        return True

    def generate_uuid(self, prefix: str='', suffix: str='') -> str:
        """
        Helper to generate uuids

        Returns
        -------
        str
            A stringified UUID
        """
        return f'{prefix}{uuid.uuid4()}{suffix}'

    def generate_android_device_id(self) -> str:
        """
        Helper to generate Android Device ID

        Returns
        -------
        str
            A random android device id
        """
        return 'android-%s' % hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]

    def with_default_data(self, data: Dict) -> Dict:
        """
        Helper to get default data

        Returns
        -------
        Dict
            A dictionary of default data
        """
        return {'_uuid': self.uuid, 'device_id': self.android_device_id, **data}

    def parse_authorization(self, authorization) -> dict:
        """Parse authorization header"""
        if not authorization:
            return {}
        try:
            b64part = authorization.rsplit(':', 1)[-1]
            if not b64part:
                return {}
            return json.loads(base64.b64decode(b64part))
        except Exception as e:
            self.logger.exception(e)
        return {}

    @property
    def authorization(self) -> str:
        """Build authorization header
        Example: Bearer IGT:2:eaW9u.....aWQiOiI0NzM5=
        """
        if self.authorization_data:
            b64part = base64.b64encode(dumps(self.authorization_data).encode()).decode()
            return f'Bearer IGT:2:{b64part}'
        return ''
