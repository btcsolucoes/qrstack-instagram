import logging
from typing import Literal, Optional
try:
    from simplejson.errors import JSONDecodeError
except ImportError:
    from json.decoder import JSONDecodeError
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
PublicTransport = Literal['requests', 'curl']
PUBLIC_WEB_APP_ID = '936619743392459'
PUBLIC_WEB_ASBD_ID = '129477'

class PublicRequestMixin:
    public_requests_count = 0
    PUBLIC_API_URL = 'https://www.instagram.com/'
    GRAPHQL_PUBLIC_API_URL = 'https://www.instagram.com/graphql/query/'
    GRAPHQL_PUBLIC_WEB_API_URL = 'https://www.instagram.com/api/graphql'
    last_public_response = None
    last_public_json = {}
    public_request_logger = logging.getLogger('public_request')
    request_timeout = 1
    public_request_retries_count = 3
    public_request_retries_timeout = 2
    session_retry_total = 3
    session_retry_backoff_factor = 2
    session_retry_statuses = [429, 500, 502, 503, 504]
    last_response_ts = 0
    public_transport = 'requests'
    public_transport_impersonate = 'chrome136'
    public_user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/11.1.2 Safari/605.1.15'
    public_curl_user_agents = {'chrome136': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'}
    public_accept_language = 'en-US'

    def __init__(self, *args, **kwargs):
        session = requests.Session()
        self.public = session
        self.public.verify = getattr(self, 'tls_verify', True)
        self.public_transport = self._normalize_public_transport(kwargs.pop('public_transport', getattr(self, 'public_transport', self.public_transport)))
        self.public_transport_impersonate = kwargs.pop('public_transport_impersonate', getattr(self, 'public_transport_impersonate', self.public_transport_impersonate))
        self.public_user_agent = kwargs.pop('public_user_agent', self._default_public_user_agent(self.public_transport, self.public_transport_impersonate))
        self.public_accept_language = kwargs.pop('public_accept_language', getattr(self, 'public_accept_language', self.public_accept_language))
        self.public.headers.update({'Connection': 'Keep-Alive', 'Accept': '*/*', 'Accept-Encoding': 'gzip,deflate', 'Accept-Language': self.public_accept_language, 'User-Agent': self.public_user_agent})
        self.request_timeout = kwargs.pop('request_timeout', getattr(self, 'request_timeout', self.request_timeout))
        self.public_request_retries_count = kwargs.pop('public_request_retries_count', getattr(self, 'public_request_retries_count', self.public_request_retries_count))
        self.public_request_retries_timeout = kwargs.pop('public_request_retries_timeout', getattr(self, 'public_request_retries_timeout', self.public_request_retries_timeout))
        self.session_retry_total = kwargs.pop('session_retry_total', getattr(self, 'session_retry_total', self.session_retry_total))
        self.session_retry_backoff_factor = kwargs.pop('session_retry_backoff_factor', getattr(self, 'session_retry_backoff_factor', self.session_retry_backoff_factor))
        self.session_retry_statuses = list(kwargs.pop('session_retry_statuses', getattr(self, 'session_retry_statuses', self.session_retry_statuses)))
        self._configure_public_session_retry()
        super().__init__(*args, **kwargs)

    @classmethod
    def _normalize_public_transport(cls, public_transport: Optional[PublicTransport]) -> PublicTransport:
        public_transport = public_transport or 'requests'
        if public_transport not in {'requests', 'curl'}:
            raise ValueError("public_transport must be 'requests' or 'curl'")
        return public_transport

    @classmethod
    def _default_public_user_agent(cls, public_transport: PublicTransport, impersonate: str) -> str:
        if public_transport == 'curl':
            return cls.public_curl_user_agents.get(impersonate, cls.public_curl_user_agents['chrome136'])
        return cls.public_user_agent

    def _build_public_session_retry_strategy(self):
        try:
            return Retry(total=self.session_retry_total, status_forcelist=self.session_retry_statuses, allowed_methods=['GET', 'POST'], backoff_factor=self.session_retry_backoff_factor, raise_on_status=False)
        except TypeError:
            return Retry(total=self.session_retry_total, status_forcelist=self.session_retry_statuses, method_whitelist=['GET', 'POST'], backoff_factor=self.session_retry_backoff_factor, raise_on_status=False)

    def _configure_public_session_retry(self):
        if self.public_transport == 'curl':
            try:
                from curl_adapter import CurlCffiAdapter
            except ImportError as exc:
                raise RuntimeError('curl public transport requires the optional curl extra: pip install instagrapi[curl]') from exc
            try:
                from curl_cffi.requests.impersonate import resolve_latest_browser_type as normalize_browser_type
            except ImportError:
                from curl_cffi.requests.impersonate import normalize_browser_type

            class BrowserCurlAdapter(CurlCffiAdapter):

                def set_curl_options(self, curl, *args, **kwargs):
                    super().set_curl_options(curl, *args, **kwargs)
                    curl.impersonate(normalize_browser_type(self.impersonate_browser_type), default_headers=True)
            adapter = BrowserCurlAdapter(impersonate_browser_type=self.public_transport_impersonate)
        else:
            adapter = HTTPAdapter(max_retries=self._build_public_session_retry_strategy())
        self.public.mount('https://', adapter)
        self.public.mount('http://', adapter)
