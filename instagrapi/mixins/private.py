import logging
import random
import time
from json.decoder import JSONDecodeError
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from instagrapi import config
from instagrapi.exceptions import AccountContactPointRequired, AccountEditError, AccountSuspended, BadPassword, ChallengeRequired, ClientBadRequestError, ClientConnectionError, ClientError, ClientForbiddenError, ClientIncompleteReadError, ClientJSONDecodeError, ClientNotFoundError, ClientRequestTimeout, ClientThrottledError, ClientUnauthorizedError, DirectMessageRequestsDisabled, FeedbackRequired, InvalidMediaId, InvalidTargetUser, LoginRequired, MediaUnavailable, PleaseWaitFewMinutes, PrivateAccount, ProxyAddressIsBlocked, RateLimitError, SentryBlock, TwoFactorRequired, UnknownError, UserNotFound, VideoTooLongException
from instagrapi.utils.auth import generate_signature
from instagrapi.utils.logging import truncate_log_text
from instagrapi.utils.serialization import dumps
_DIRECT_MESSAGE_REQUESTS_DISABLED_MARKERS = ("can't message this account unless they follow you", "can't receive your message because they don't allow new message requests", "doesn't allow new message requests", "don't allow new message requests", 'does not allow new message requests')
_ACCOUNT_CONTACT_POINT_REQUIRED_MARKERS = ('need an email or confirmed phone number',)
_LOGIN_CONTEXT_REJECTION_HINT = 'This can also happen when Instagram rejects the proxy/IP, device fingerprint, or login context, even if the password is correct.'

def _private_message_text(message) -> str:
    if isinstance(message, dict):
        errors = message.get('errors')
        if isinstance(errors, (list, tuple)):
            return ' '.join((str(error) for error in errors))
        return ' '.join((str(value) for value in message.values()))
    if isinstance(message, (list, tuple)):
        return ' '.join((str(item) for item in message))
    return str(message or '')

def _is_account_contact_point_required(endpoint: str, message) -> bool:
    if not endpoint or 'accounts/edit_profile' not in endpoint:
        return False
    normalized = _private_message_text(message).casefold()
    return any((marker in normalized for marker in _ACCOUNT_CONTACT_POINT_REQUIRED_MARKERS))

def _is_account_edit_error(endpoint: str) -> bool:
    return bool(endpoint and 'accounts/edit_profile' in endpoint)

def _is_direct_message_requests_disabled(endpoint: str, message: str) -> bool:
    if not endpoint or not message or 'direct_v2/' not in endpoint:
        return False
    normalized = str(message).casefold().replace('’', "'")
    return any((marker in normalized for marker in _DIRECT_MESSAGE_REQUESTS_DISABLED_MARKERS))

def manual_input_code(self, username: str, choice=None):
    """
    Manual security code helper

    Parameters
    ----------
    username: str
        User name of a Instagram account
    choice: optional
        Whether sms or email

    Returns
    -------
    str
        Code
    """
    code = None
    while True:
        code = input(f'Enter code (6 digits) for {username} ({choice}): ').strip()
        if code and code.isdigit():
            break
    return code

def manual_change_password(self, username: str):
    pwd = None
    while not pwd:
        pwd = input(f'Enter password for {username}: ').strip()
    return pwd

class PrivateRequestMixin:
    """
    Helpers for private request
    """
    private_requests_count = 0
    handle_exception = None
    challenge_code_handler = manual_input_code
    change_password_handler = manual_change_password
    private_request_logger = logging.getLogger('private_request')
    request_timeout = 1
    read_timeout = 25
    session_retry_total = 3
    session_retry_backoff_factor = 2
    session_retry_statuses = [429, 500, 502, 503, 504]
    private_transport = 'curl'
    domain = config.API_DOMAIN
    last_response = None
    last_json = {}

    def __init__(self, *args, **kwargs):
        session = requests.Session()
        self.private = session
        self.private.verify = getattr(self, 'tls_verify', True)
        self.private_transport = self._normalize_private_transport(kwargs.pop('private_transport', self.private_transport))
        self.email = kwargs.pop('email', None)
        self.phone_number = kwargs.pop('phone_number', None)
        self.request_timeout = kwargs.pop('request_timeout', getattr(self, 'request_timeout', self.request_timeout))
        self.session_retry_total = kwargs.pop('session_retry_total', getattr(self, 'session_retry_total', self.session_retry_total))
        self.session_retry_backoff_factor = kwargs.pop('session_retry_backoff_factor', getattr(self, 'session_retry_backoff_factor', self.session_retry_backoff_factor))
        self.session_retry_statuses = list(kwargs.pop('session_retry_statuses', getattr(self, 'session_retry_statuses', self.session_retry_statuses)))
        self._configure_private_session_retry()
        super().__init__(*args, **kwargs)

    def _build_private_session_retry_strategy(self):
        try:
            return Retry(total=self.session_retry_total, status_forcelist=self.session_retry_statuses, allowed_methods=['GET', 'POST'], backoff_factor=self.session_retry_backoff_factor, raise_on_status=False)
        except TypeError:
            return Retry(total=self.session_retry_total, status_forcelist=self.session_retry_statuses, method_whitelist=['GET', 'POST'], backoff_factor=self.session_retry_backoff_factor, raise_on_status=False)

    @staticmethod
    def _normalize_private_transport(private_transport):
        if private_transport not in {'requests', 'curl'}:
            raise ValueError("private_transport must be 'requests' or 'curl'")
        return private_transport

    def _configure_private_session_retry(self, private_transport=None):
        private_transport = self.private_transport if private_transport is None else private_transport
        if private_transport == 'curl':
            if getattr(self, '_private_adapter_transport', None) == 'curl':
                return
            from instagrapi.transports import create_curl_h2_adapter
            adapter = create_curl_h2_adapter()
        else:
            adapter = HTTPAdapter(max_retries=self._build_private_session_retry_strategy())
        previous = set(self.private.adapters.values())
        self.private.mount('https://', adapter)
        self.private.mount('http://', adapter)
        self.private_transport = private_transport
        self._private_adapter_transport = private_transport
        for old_adapter in previous:
            if old_adapter not in self.private.adapters.values():
                old_adapter.close()

    @property
    def base_headers(self):
        locale = self.locale.replace('-', '_')
        accept_language = ['en-US']
        if locale:
            lang = locale.replace('_', '-')
            if lang not in accept_language:
                accept_language.insert(0, lang)
        headers = {'X-IG-App-Locale': locale, 'X-IG-Device-Locale': locale, 'X-IG-Mapped-Locale': locale, 'X-Pigeon-Session-Id': self.generate_uuid('UFS-', '-1'), 'X-Pigeon-Rawclienttime': str(round(time.time(), 3)), 'X-IG-Bandwidth-Speed-KBPS': str(random.randint(2500000, 3000000) / 1000), 'X-IG-Bandwidth-TotalBytes-B': str(random.randint(5000000, 90000000)), 'X-IG-Bandwidth-TotalTime-MS': str(random.randint(2000, 9000)), 'X-IG-App-Startup-Country': self.country.upper(), 'X-Bloks-Version-Id': self.bloks_versioning_id, 'X-IG-WWW-Claim': '0', 'X-Bloks-Is-Layout-RTL': 'false', 'X-Bloks-Is-Panorama-Enabled': 'true', 'X-IG-Device-ID': self.uuid, 'X-IG-Family-Device-ID': self.phone_id, 'X-IG-Android-ID': self.android_device_id, 'X-IG-Timezone-Offset': str(self.timezone_offset), 'X-IG-Connection-Type': 'WIFI', 'X-IG-Capabilities': '3brTv10=', 'X-IG-App-ID': self.app_id, 'Priority': 'u=3', 'User-Agent': self.user_agent, 'Accept-Language': ', '.join(accept_language), 'X-MID': self.mid, 'Accept-Encoding': 'gzip, deflate', 'Host': self.domain, 'X-FB-HTTP-Engine': 'Tigon/MNS/TCP', 'X-Tigon-Is-Retry': 'False', 'X-Zero-Balance': 'INIT', 'X-Zero-Eh': '', 'X-Zero-State': 'unknown', 'Zero-HTTP-Network-Interface': 'wifi', 'Connection': 'keep-alive', 'X-FB-Client-IP': 'True', 'X-FB-Server-Cluster': 'True', 'IG-INTENDED-USER-ID': str(self.user_id or 0), 'X-IG-Nav-Chain': '9MV:self_profile:2,ProfileMediaTabFragment:self_profile:3,9Xf:self_following:4', 'X-IG-SALT-IDS': str(random.randint(1061162222, 1061262222))}
        if self.user_id:
            next_year = time.time() + 31536000
            headers.update({'IG-U-DS-USER-ID': str(self.user_id), 'IG-U-IG-DIRECT-REGION-HINT': f'LLA,{self.user_id},{next_year}:01f7bae7d8b131877d8e0ae1493252280d72f6d0d554447cb1dc9049b6b2c507c08605b7', 'IG-U-SHBID': f'12695,{self.user_id},{next_year}:01f778d9c9f7546cf3722578fbf9b85143cd6e5132723e5c93f40f55ca0459c8ef8a0d9f', 'IG-U-SHBTS': f'{int(time.time())},{self.user_id},{next_year}:01f7ace11925d0388080078d0282b75b8059844855da27e23c90a362270fddfb3fae7e28', 'IG-U-RUR': f'RVA,{self.user_id},{next_year}:01f7f627f9ae4ce2874b2e04463efdb184340968b1b006fa88cb4cc69a942a04201e544c'})
        if self.ig_u_rur:
            headers.update({'IG-U-RUR': self.ig_u_rur})
        if self.ig_www_claim:
            headers.update({'X-IG-WWW-Claim': self.ig_www_claim})
        if self.usdid_private_key:
            headers.update({'X-Meta-Usdid': self.usdid_header()})
        return headers

    def private_headers(self, headers=None):
        request_headers = dict(self.base_headers)
        if headers:
            request_headers.update(headers)
        if self.authorization and (not any((key.lower() == 'authorization' for key in request_headers))):
            request_headers['Authorization'] = self.authorization
        return request_headers

    def set_country(self, country: str='US'):
        """Set you country code (ISO 3166-1/3166-2)

        Parameters
        ----------
        country: str
            Your country code (ISO 3166-1/3166-2) string identifier (e.g. US, UK, RU)
            Advise to specify the country code of your proxy

        Returns
        -------
        bool
            A boolean value
        """
        self.settings['country'] = self.country = str(country)
        return True

    def set_country_code(self, country_code: int=1):
        """Set country calling code

        Parameters
        ----------
        country_code: int

        Returns
        -------
        bool
            A boolean value
        """
        self.settings['country_code'] = self.country_code = int(country_code)
        return True

    def set_locale(self, locale: str='en_US'):
        """Set you locale (ISO 3166-1/3166-2)

        Parameters
        ----------
        locale: str
            Your locale code (ISO 3166-1/3166-2) string identifier (e.g. US, UK, RU)
            Advise to specify the locale code of your proxy

        Returns
        -------
        bool
            A boolean value
        """
        user_agent = (self.settings.get('user_agent') or '').replace(self.locale, locale)
        self.settings['locale'] = self.locale = str(locale)
        self.set_user_agent(user_agent)
        if '_' in locale:
            self.set_country(locale.rsplit('_', 1)[1])
        return True

    @staticmethod
    def _timezone_name_from_offset(seconds: int) -> str:
        seconds = int(seconds)
        if seconds == 0:
            return 'GMT'
        sign = '+' if seconds > 0 else '-'
        seconds = abs(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f'GMT{sign}{hours:02d}:{minutes:02d}'

    def set_timezone_name(self, timezone_name: str=''):
        """Set your timezone name for request metadata."""
        self.settings['timezone_name'] = self.timezone_name = str(timezone_name)
        return True

    def set_timezone_offset(self, seconds: int=0, timezone_name=None):
        """Set you timezone offset in seconds

        Parameters
        ----------
        seconds: int
            Specify the offset in seconds from UTC

        Returns
        -------
        bool
            A boolean value
        """
        self.settings['timezone_offset'] = self.timezone_offset = int(seconds)
        self.set_timezone_name(self._timezone_name_from_offset(self.timezone_offset) if timezone_name is None else timezone_name)
        return True

    def set_push_disabled(self, disabled: bool=True):
        """Configure whether request metadata reports push as disabled."""
        self.settings['push_disabled'] = self.push_disabled = bool(disabled)
        return True

    def set_ig_u_rur(self, value):
        self.settings['ig_u_rur'] = self.ig_u_rur = value
        return True

    def set_ig_www_claim(self, value):
        self.settings['ig_www_claim'] = self.ig_www_claim = value
        return True

    def _send_private_request(self, endpoint, data=None, params=None, login=False, with_signature=True, headers=None, extra_sig=None, domain: str=None):
        self.last_response = None
        self.last_json = last_json = {}
        self.private.headers.update(self.base_headers)
        request_headers = dict(headers) if headers else {}
        if domain:
            request_headers['Host'] = domain
        if not login:
            time.sleep(self.request_timeout)
        try:
            if not endpoint.startswith('/'):
                endpoint = f'/v1/{endpoint}'
            if endpoint == '/challenge/':
                endpoint = '/v1/challenge/'
            api_url = f'https://{domain or config.API_DOMAIN}/api{endpoint}'
            self.logger.info(api_url)
            if data:
                self.private.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
                if with_signature:
                    data = generate_signature(dumps(data))
                    if extra_sig:
                        data += '&'.join(extra_sig)
                response = self.private.post(api_url, data=data, params=params, headers=request_headers or None, proxies=self.private.proxies, timeout=self.read_timeout, allow_redirects=False)
            else:
                self.private.headers.pop('Content-Type', None)
                response = self.private.get(api_url, params=params, headers=request_headers or None, proxies=self.private.proxies, timeout=self.read_timeout, allow_redirects=False)
            self.logger.debug('private_request %s: %s (%s)', response.status_code, response.url, response.text)
            mid = response.headers.get('ig-set-x-mid')
            if mid:
                self.mid = mid
            self.request_log(response)
            self.last_response = response
            response.raise_for_status()
            self.last_json = last_json = response.json()
            self.logger.debug('last_json %s', last_json)
        except JSONDecodeError as e:
            self.logger.error('Status %s: JSONDecodeError in private_request (user_id=%s, endpoint=%s) >>> %s', response.status_code, self.user_id, endpoint, truncate_log_text(response.text))
            raise ClientJSONDecodeError('JSONDecodeError {0!s} while opening {1!s}'.format(e, response.url), response=response)
        except requests.HTTPError as e:
            try:
                self.last_json = last_json = response.json()
            except ValueError:
                pass
            message = last_json.get('message', '')
            if 'Please wait a few minutes' in message:
                raise PleaseWaitFewMinutes(e, response=e.response, **last_json)
            if e.response.status_code == 403:
                if message == 'login_required':
                    raise LoginRequired(response=e.response, **last_json)
                if len(e.response.text) < 512:
                    last_json['message'] = e.response.text
                raise ClientForbiddenError(e, response=e.response, **last_json)
            elif e.response.status_code == 400:
                error_type = last_json.get('error_type')
                if last_json.get('two_factor_info'):
                    if not last_json.get('message'):
                        last_json['message'] = 'Two-factor authentication required'
                        if last_json.get('error_type') != 'two_factor_required':
                            self.logger.info('Changing error_type from %s to two_factor_required due to presence of two_factor_info', last_json.get('error_type'))
                        last_json['error_type'] = 'two_factor_required'
                    raise TwoFactorRequired(**last_json)
                elif message == 'challenge_required':
                    challenge = last_json.get('challenge') or {}
                    if '/suspended/' in (challenge.get('url') or ''):
                        raise AccountSuspended(**last_json)
                    raise ChallengeRequired(**last_json)
                elif message == 'feedback_required':
                    raise FeedbackRequired(**dict(last_json, message='%s: %s' % (message, last_json.get('feedback_message'))))
                elif error_type == 'sentry_block':
                    raise SentryBlock(**last_json)
                elif error_type == 'rate_limit_error':
                    raise RateLimitError(**last_json)
                elif error_type == 'bad_password':
                    last_json['message'] = ' '.join((part for part in (last_json.get('message') or 'Instagram rejected the login credentials.', _LOGIN_CONTEXT_REJECTION_HINT) if part))
                    raise BadPassword(**last_json)
                elif error_type == 'two_factor_required':
                    if not last_json['message']:
                        last_json['message'] = 'Two-factor authentication required'
                    raise TwoFactorRequired(**last_json)
                elif _is_account_contact_point_required(endpoint, message):
                    raise AccountContactPointRequired(e, response=e.response, **last_json)
                elif _is_account_edit_error(endpoint):
                    raise AccountEditError(e, response=e.response, **last_json)
                elif _is_direct_message_requests_disabled(endpoint, message):
                    raise DirectMessageRequestsDisabled(e, response=e.response, **last_json)
                elif 'VideoTooLongException' in message:
                    raise VideoTooLongException(e, response=e.response, **last_json)
                elif 'Not authorized to view user' in message:
                    raise PrivateAccount(e, response=e.response, **last_json)
                elif 'Invalid target user' in message:
                    raise InvalidTargetUser(e, response=e.response, **last_json)
                elif 'Invalid media_id' in message:
                    raise InvalidMediaId(e, response=e.response, **last_json)
                elif 'Media is unavailable' in message or 'Media not found or unavailable' in message:
                    raise MediaUnavailable(e, response=e.response, **last_json)
                elif 'has been deleted' in message:
                    raise MediaUnavailable(e, response=e.response, **last_json)
                elif 'unable to fetch followers' in message:
                    raise UserNotFound(e, response=e.response, **last_json)
                elif 'The username you entered' in message:
                    last_json['message'] = 'Instagram has blocked your IP address, use a quality proxy provider (not free, not shared)'
                    raise ProxyAddressIsBlocked(**last_json)
                elif error_type or message:
                    raise UnknownError(**last_json)
                self.logger.exception(e)
                self.logger.warning('Status 400: %s', message or 'Empty response message. Maybe enabled Two-factor auth?')
                raise ClientBadRequestError(e, response=e.response, **last_json)
            elif e.response.status_code == 429:
                self.logger.warning('Status 429: Too many requests')
                raise ClientThrottledError(e, response=e.response, **last_json)
            elif e.response.status_code == 401:
                self.logger.warning('Status 401: Unauthorized %s', endpoint)
                raise ClientUnauthorizedError(e, response=e.response, **last_json)
            elif e.response.status_code == 404:
                if e.response.content == b'Not Found':
                    self.logger.warning('Status 404 (masked challenge): %s', endpoint)
                    raise ChallengeRequired(**last_json)
                self.logger.warning('Status 404: Endpoint %s does not exist', endpoint)
                raise ClientNotFoundError(e, response=e.response, **last_json)
            elif e.response.status_code == 408:
                self.logger.warning('Status 408: Request Timeout')
                raise ClientRequestTimeout(e, response=e.response, **last_json)
            raise ClientError(e, response=e.response, **last_json)
        except requests.exceptions.ChunkedEncodingError as e:
            raise ClientIncompleteReadError('{} {}'.format(e.__class__.__name__, str(e))) from e
        except requests.ConnectionError as e:
            raise ClientConnectionError('{e.__class__.__name__} {e}'.format(e=e))
        if last_json.get('status') == 'fail':
            message = last_json.get('message', '')
            if _is_account_contact_point_required(endpoint, message):
                raise AccountContactPointRequired(response=response, **last_json)
            if _is_account_edit_error(endpoint):
                raise AccountEditError(response=response, **last_json)
            if _is_direct_message_requests_disabled(endpoint, message):
                raise DirectMessageRequestsDisabled(response=response, **last_json)
            raise ClientError(response=response, **last_json)
        elif 'error_title' in last_json:
            "Example: {\n            'error_title': 'bad image input extra:{}', <-------------\n            'media': {\n                'device_timestamp': '1588184737203',\n                'upload_id': '1588184737203'\n            },\n            'message': 'media_needs_reupload', <-------------\n            'status': 'ok' <-------------\n            }"
            raise ClientError(response=response, **last_json)
        return last_json
