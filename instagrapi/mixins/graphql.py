import json
import logging
import time
from json.decoder import JSONDecodeError
from typing import Dict, Optional
import requests
from instagrapi.exceptions import ChallengeRequired, ClientBadRequestError, ClientConnectionError, ClientError, ClientForbiddenError, ClientGraphqlError, ClientJSONDecodeError, ClientNotFoundError, ClientThrottledError, ClientUnauthorizedError, FeedbackRequired, LoginRequired, PrivateAccount, RateLimitError, SentryBlock, UserNotFound
GRAPHQL_API_URL = 'https://www.instagram.com/api/graphql'
PRIVATE_GRAPHQL_QUERY_URL = 'https://i.instagram.com/graphql/query'
PRIVATE_GRAPHQL_WWW_DOMAIN = 'b.i.instagram.com'
GQL_STUFF = {'av': '17841464591314721', '__d': 'www', '__user': '0', '__a': '1', '__req': 'q', '__hs': '19768.HYP:instagram_web_pkg.2.1..0.1', 'dpr': '2', '__ccg': 'UNKNOWN', '__rev': '1011444902', '__s': 'x82a1q:agr3gd:4nh4nl', '__hsi': '7335888108907652597', '__dyn': '7xeUjG1mxu1syUbFp40NonwgU7SbzEdF8aUco2qwJxS0k24o0B-q1ew65xO0FE2awt81s8hwGwQwoEcE7O2l0Fwqo31w9O7U2cxe0EUjwGzEaE7622362W2K0zK5o4q3y1Sx-0iS2Sq2-azqwt8dUaob82cwMwrUdUbGwmk0KU6O1FwlE6PhA6bxy4VUKUnAwHw', '__csr': 'g9cj5kxfs8lifTitQDqhdhalmDEAJaKBRJFdkAGHBkPy9HgCA-Artucm5bCBBGpyAoz-mLJpXJufKWGQ9hHhAhnKECuFUZ3Q8JkmmpeWyGAzkEj_CjyoZUgK-E8bwYzaxy00ktMGx20XU3gw4KAo3MChUjw3N80poolwiA1d7G2yu2ucxi1nwEw16OE1JsS043Etw63wkSEgg1Mu00yiU', '__comet_req': '7', 'lsd': '6b2800R9u4biJOYjcdXFEI', '__spin_r': '1011444902', '__spin_b': 'trunk', '__spin_t': '1708019550', 'fb_api_caller_class': 'RelayModern', 'fb_api_req_friendly_name': 'PolarisPostCommentsPaginationQuery', 'server_timestamps': 'true'}

class PrivateGraphQLRequestMixin:
    _fb_dtsg = None
    graphql_requests_count = 0
    last_graphql_response = None
    last_graphql_json = {}
    request_logger = logging.getLogger('graphql_request')

    def __init__(self, *args, **kwargs):
        self.graphql = requests.Session()
        self.graphql.verify = getattr(self, 'tls_verify', True)
        self.graphql.headers.update({'Connection': 'Keep-Alive', 'Accept': '*/*', 'Accept-Encoding': 'gzip,deflate', 'Accept-Language': 'en-US,en;q=0.9', 'origin': 'https://www.instagram.com', 'authority': 'www.instagram.com', 'sec-fetch-site': 'same-origin', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'})
        super().__init__(*args, **kwargs)

    def _merge_incremental_graphql_payload(self, base: Dict, payload: Dict) -> None:
        path = payload.get('path')
        if not isinstance(path, list) or not path or 'data' not in payload:
            return
        target = base
        if path and path[0] != 'data':
            target = base.get('data', {})
        elif path and path[0] == 'data':
            path = path[1:]
        if not path:
            return
        try:
            for key in path[:-1]:
                target = target[key]
        except (KeyError, IndexError, TypeError):
            return
        key = path[-1]
        value = payload['data']
        if isinstance(target, list):
            if not isinstance(key, int):
                return
            try:
                current = target[key]
            except IndexError:
                return
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                target[key] = value
            return
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        else:
            target[key] = value

    def _json_from_graphql_response(self, response):
        try:
            return response.json()
        except JSONDecodeError:
            chunks = [json.loads(line) for line in response.text.splitlines() if line.strip()]
            if not chunks:
                raise
            body = chunks[0]
            for chunk in chunks[1:]:
                self._merge_incremental_graphql_payload(body, chunk)
            return body

    def _raise_graphql_http_error(self, exc, response):
        """Map a failed private GraphQL HTTP response to a typed exception.

        Shared by ``private_graphql_query_request`` and ``private_graphql_www_request``
        so both surface ``LoginRequired`` / ``ChallengeRequired`` / ``FeedbackRequired``
        / ``RateLimitError`` and status-coded errors instead of a generic ``ClientError``.
        Always raises.
        """
        last_json = {}
        try:
            body_json = response.json()
            if isinstance(body_json, dict):
                self.last_json = body_json
                last_json = body_json
        except Exception:
            last_json = {}
        message = (last_json.get('message') or '').lower()
        error_type = last_json.get('error_type')
        if message == 'login_required':
            raise LoginRequired(exc, response=response, **last_json)
        if message == 'challenge_required':
            raise ChallengeRequired(**last_json)
        if message == 'feedback_required':
            raise FeedbackRequired(exc, response=response, **last_json)
        if error_type == 'rate_limit_error':
            raise RateLimitError(exc, response=response, **last_json)
        if message == 'user_blocked':
            raise SentryBlock(exc, response=response, **last_json)
        if 'not authorized to view user' in message:
            raise PrivateAccount(exc, response=response, **last_json)
        if 'unable to fetch followers' in message or 'error generating user info response' in message:
            raise UserNotFound(exc, response=response, **last_json)
        if getattr(response, 'status_code', None) == 404 and getattr(response, 'content', None) == b'Not Found':
            raise ChallengeRequired(**last_json)
        status_code = getattr(response, 'status_code', None)
        exc_cls = {400: ClientBadRequestError, 401: ClientUnauthorizedError, 403: ClientForbiddenError, 404: ClientNotFoundError, 429: ClientThrottledError}.get(status_code, ClientError)
        raise exc_cls(exc, response=response, **last_json)

    def private_graphql_www_request(self, friendly_name: str, variables: Optional[Dict]=None, client_doc_id: Optional[str]=None, domain: str=PRIVATE_GRAPHQL_WWW_DOMAIN, extra_headers: Optional[Dict]=None, purpose: Optional[str]='fetch') -> Dict:
        data = {'method': 'post', 'pretty': 'false', 'format': 'json', 'server_timestamps': 'true', 'locale': 'user', 'fb_api_req_friendly_name': friendly_name, 'enable_canonical_naming': 'true', 'enable_canonical_variable_overrides': 'true', 'enable_canonical_naming_ambiguous_type_prefixing': 'true', 'variables': json.dumps(variables or {}, separators=(',', ':'))}
        if purpose is not None:
            data['purpose'] = purpose
        if client_doc_id:
            data['client_doc_id'] = str(client_doc_id)
        headers = {'X-FB-Friendly-Name': friendly_name, 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
        if client_doc_id:
            headers['X-Client-Doc-Id'] = str(client_doc_id)
        if extra_headers:
            headers.update(extra_headers)
        merged = dict(self.base_headers)
        merged.update(headers)
        merged['Host'] = domain
        if self.authorization:
            merged.setdefault('Authorization', self.authorization)
        if self.request_timeout:
            time.sleep(self.request_timeout)
        url = f'https://{domain}/graphql_www'
        response = None
        try:
            self.private_requests_count += 1
            response = self.private.post(url, data=data, headers=merged, proxies=self.private.proxies)
            self.request_log(response)
            self.last_response = response
            response.raise_for_status()
            self.last_json = self._json_from_graphql_response(response)
        except JSONDecodeError as exc:
            url = response.url if response else url
            raise ClientJSONDecodeError('JSONDecodeError {0!s} while opening {1!s}'.format(exc, url), response=response)
        except requests.HTTPError as exc:
            self._raise_graphql_http_error(exc, response)
        except requests.ConnectionError as exc:
            raise ClientConnectionError('{} {}'.format(exc.__class__.__name__, str(exc)))
        if self.last_json.get('errors'):
            raise ClientGraphqlError(self.last_json.get('errors'))
        if self.last_json.get('status') == 'fail':
            raise ClientError(response=response, **self.last_json)
        return self.last_json
