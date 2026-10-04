import json
import re
import time
from http.cookies import SimpleCookie
from json import JSONDecodeError
from typing import Any, Dict, List, Optional
from uuid import uuid4
from instagrapi.exceptions import ClientError
from instagrapi.utils.serialization import dumps
CAA_API_DOMAIN = 'b.i.instagram.com'
AP_2SV_ENTRYPOINT = 'com.bloks.www.ap.two_step_verification.entrypoint_async'
AP_2SV_CODE_ENTRY = 'com.bloks.www.ap.two_step_verification.code_entry'
AP_2SV_CODE_ENTRY_ASYNC = 'com.bloks.www.ap.two_step_verification.code_entry_async'

class BloksMixin:
    bloks_versioning_id = ''
    caa_aac = ''
    caa_waterfall_id = ''

    def _bloks_payload(self, params: Dict, bloks_versioning_id: str='') -> Dict[str, str]:
        versioning_id = bloks_versioning_id or self.bloks_versioning_id
        assert versioning_id, 'Client.bloks_versioning_id is empty (hash is expected)'
        return {'params': dumps(params), '_uuid': self.uuid, 'bk_client_context': dumps({'bloks_version': versioning_id, 'styles_id': 'instagram'}), 'bloks_versioning_id': versioning_id}

    def bloks_async_action(self, action: str, params: Dict, bloks_versioning_id: str='', domain: Optional[str]=None, extra_headers: Optional[Dict[str, str]]=None, login: bool=False) -> Dict:
        """
        Perform a raw Bloks async action.

        Parameters
        ----------
        action: str
            Async action, for example ``com.bloks.www.fxcal.link.async``.
        params: Dict
            Bloks ``params`` payload.
        bloks_versioning_id: str, optional
            Bloks versioning id. Uses ``Client.bloks_versioning_id`` when omitted.
        domain: str, optional
            API domain override. Uses the default private API domain when omitted.
        extra_headers: Dict[str, str], optional
            Per-request HTTP headers.
        login: bool, optional
            Send the request without the normal post-login delay.

        Returns
        -------
        Dict
            Raw Instagram response.
        """
        data = self._bloks_payload(params, bloks_versioning_id=bloks_versioning_id)
        headers = {'X-FB-Friendly-Name': f'IgApi: bloks/async_action/{action}/'}
        if extra_headers:
            headers.update(extra_headers)
        kwargs = {'data': data, 'with_signature': False, 'headers': headers}
        if domain:
            kwargs['domain'] = domain
        if login:
            kwargs['login'] = True
        return self.private_request(f'bloks/async_action/{action}/', **kwargs)

    def _caa_device_network_info(self) -> Dict[str, Any]:
        return {'active_subscriptions_info': None, 'default_subscription_info': {'network_type': None, 'is_data_roaming': 1, 'is_esim': None, 'is_gsm_roaming': 0, 'is_sim_sms_capable': None, 'is_mobile_data_enabled': 1, 'sim_carrier_id': 1, 'sim_carrier_id_name': None, 'sim_state': 5, 'sim_operator': '310260', 'sim_operator_name': 'T-Mobile', 'signal_strength': None, 'group_id_level_1': None, 'network_operator': '310260'}, 'is_airplane_mode': 0, 'is_active_network_cellular': 0, 'is_device_sms_capable': 1, 'sim_count': 1, 'is_wifi': 1}

    def bloks_extract_aac(self, result: Dict) -> str:
        """Extract the server-issued CAA account access context."""
        data = result.get('layout', {}).get('bloks_payload', {}).get('data', [])
        if not isinstance(data, list):
            return ''
        for node in data:
            payload = node.get('data') if isinstance(node, dict) else None
            if not isinstance(payload, dict) or payload.get('key') != 'CAA_ACCOUNT_ACCESS_CONTEXT:aac':
                continue
            initial = payload.get('initial')
            if isinstance(initial, str) and initial.strip():
                return initial
            lispy = payload.get('initial_lispy')
            if isinstance(lispy, str) and lispy:
                value = self._extract_first_json_string(lispy, 0)
                if value:
                    return value
        return ''

    def bloks_caa_login_process_client_data(self, waterfall_id: str='', offline_experiment_group: str='caa_iteration_v3_perf_ig_4', bloks_versioning_id: str='', domain: Optional[str]=None) -> Dict:
        """Open the CAA login homepage and retain its server-issued ``aac``."""
        self.caa_waterfall_id = waterfall_id or str(uuid4())
        self.caa_aac = ''
        params = {'is_from_logged_out': False, 'logged_out_user': '', 'qpl_join_id': None, 'family_device_id': self.phone_id, 'device_id': self.android_device_id, 'offline_experiment_group': offline_experiment_group, 'waterfall_id': self.caa_waterfall_id, 'logout_source': '', 'show_internal_settings': False, 'last_auto_login_time': 0, 'disable_auto_login': False, 'qe_device_id': self.uuid, 'use_auto_login_interstitial': True, 'disable_recursive_auto_login_interstitial': True, 'auto_login_interstitial_experiment_group_name': '', 'is_from_logged_in_switcher': False, 'switcher_logged_in_uid': '', 'account_list': [], 'blocked_uid': [], 'INTERNAL_INFRA_THEME': 'THREE_NEUTRAL_GRAY', 'layered_homepage_experiment_group': 'Deploy: Not in Experiment', 'launched_url': '', 'sim_phone_numbers': [], 'is_from_registration_reminder': False}
        result = self.bloks_async_action('com.bloks.www.bloks.caa.login.process_client_data_and_redirect', params, bloks_versioning_id=bloks_versioning_id, domain=domain, login=True)
        self.caa_aac = self.bloks_extract_aac(result)
        return result

    def bloks_caa_login_oauth_token_fetch(self, username: str='', waterfall_id: str='', offline_experiment_group: str='caa_iteration_v3_perf_ig_4', bloks_versioning_id: str='', domain: Optional[str]=None) -> Dict:
        """Perform the CAA OAuth token preflight sent before credentials."""
        self.caa_waterfall_id = waterfall_id or self.caa_waterfall_id or str(uuid4())
        params = {'client_input_params': {'username_input': username or self.username, 'si_device_param_network_info': self._caa_device_network_info(), 'aac': self.caa_aac, 'lois_settings': {'lois_token': ''}, 'cloud_trust_token': None, 'zero_balance_state': '', 'network_bssid': None}, 'server_params': {'is_from_logged_out': 0, 'layered_homepage_experiment_group': 'Deploy: Not in Experiment', 'device_id': self.android_device_id, 'login_surface': 'login_home', 'waterfall_id': self.caa_waterfall_id, 'INTERNAL__latency_qpl_instance_id': int(time.time() * 1000), 'is_platform_login': 0, 'login_entry_point': 'logged_out', 'INTERNAL__latency_qpl_marker_id': 36707139, 'family_device_id': self.phone_id, 'offline_experiment_group': offline_experiment_group, 'access_flow_version': 'pre_mt_behavior', 'is_from_logged_in_switcher': 0, 'qe_device_id': self.uuid}}
        return self.bloks_async_action('com.bloks.www.caa.login.oauth.token.fetch.async', params, bloks_versioning_id=bloks_versioning_id, domain=domain, login=True)

    def bloks_caa_login_prepare(self, username: str='', domain: Optional[str]=None, waterfall_id: str='', offline_experiment_group: str='caa_iteration_v3_perf_ig_4', bloks_versioning_id: str='') -> bool:
        """Run the ordered device and CAA preflight needed before login."""
        if not self.usdid_registered and (not self.usdid_register()):
            return False
        self.bloks_caa_login_process_client_data(waterfall_id=waterfall_id, offline_experiment_group=offline_experiment_group, bloks_versioning_id=bloks_versioning_id, domain=domain)
        self.attestation_challenge_nonce = ''
        self.attestation_key_nonce = ''
        self.attestation_create_android_keystore(domain=domain)
        if self.caa_aac:
            self.bloks_caa_login_oauth_token_fetch(username=username, waterfall_id=waterfall_id, offline_experiment_group=offline_experiment_group, bloks_versioning_id=bloks_versioning_id, domain=domain)
        return bool(self.caa_aac and self.attestation_challenge_nonce)

    def bloks_caa_login_send_request(self, password: str, username: str='', login_attempt_count: int=1, try_num: int=1, waterfall_id: str='', offline_experiment_group: str='caa_iteration_v3_perf_ig_4', bloks_versioning_id: str='', domain: Optional[str]=None, auto_prepare: bool=True) -> Dict:
        """
        Send the current CAA/Bloks login request used before Bloks 2FA.

        When needed, this low-level helper obtains the server-issued account
        access context through :meth:`bloks_caa_login_prepare` before encrypting
        the password. Set ``auto_prepare=False`` to require pre-existing state.

        Returns
        -------
        Dict
            Raw Instagram response.
        """
        contact_point = username or self.username
        if auto_prepare and (not self.caa_aac):
            preflight_state = (self.caa_aac, self.caa_waterfall_id, self.attestation_challenge_nonce, self.attestation_key_nonce)
            try:
                self.bloks_caa_login_prepare(username=contact_point, domain=domain, waterfall_id=waterfall_id, offline_experiment_group=offline_experiment_group, bloks_versioning_id=bloks_versioning_id)
            except Exception:
                self.caa_aac, self.caa_waterfall_id, self.attestation_challenge_nonce, self.attestation_key_nonce = preflight_state
                raise
        if not self.caa_aac:
            raise ClientError('CAA login requires a server-issued aac; call bloks_caa_login_prepare() before send_login_request')
        encrypted_password = password if password.startswith('#PWD_') else self.password_encrypt(password)
        flow_id = waterfall_id or self.caa_waterfall_id or str(uuid4())
        self.caa_waterfall_id = flow_id
        text_input_id = f'{uuid4().hex[:4]}ig'
        params = {'client_input_params': {'blocked_uids': [], 'aac': self.caa_aac, 'sim_phones': [], 'aymh_accounts': [], 'network_bssid': None, 'secure_family_device_id': '', 'has_granted_read_contacts_permissions': 0, 'auth_secure_device_id': '', 'has_whatsapp_installed': 0, 'si_device_param_network_info': self._caa_device_network_info(), 'password': encrypted_password, 'sso_token_map_json_string': '', 'block_store_machine_id': '', 'ig_vetted_device_nonces': None, 'cloud_trust_token': None, 'event_flow': 'login_manual', 'password_contains_non_ascii': str(not password.isascii()).lower(), 'client_known_key_hash': '', 'sso_accounts_auth_data': [], 'encrypted_msisdn': '', 'has_granted_read_phone_permissions': 0, 'app_manager_id': '', 'should_show_nested_nta_from_aymh': 0, 'device_id': self.android_device_id, 'zero_balance_state': '', 'login_attempt_count': login_attempt_count, 'machine_id': self.mid, 'flash_call_permission_status': {'READ_PHONE_STATE': 'DENIED', 'READ_CALL_LOG': 'DENIED', 'ANSWER_PHONE_CALLS': 'DENIED'}, 'accounts_list': [], 'gms_incoming_call_retriever_eligibility': 'eligible', 'family_device_id': self.phone_id, 'fb_ig_device_id': [], 'device_emails': [], 'try_num': try_num, 'lois_settings': {'lois_token': ''}, 'event_step': 'home_page', 'headers_infra_flow_id': '', 'openid_tokens': {}, 'contact_point': contact_point}, 'server_params': {'should_trigger_override_login_2fa_action': 0, 'is_from_logged_out': 0, 'should_trigger_override_login_success_action': 0, 'login_credential_type': 'none', 'server_login_source': 'login', 'waterfall_id': flow_id, 'two_step_login_type': 'one_step_login', 'login_source': 'Login', 'is_platform_login': 0, 'login_entry_point': 'logged_out', 'INTERNAL__latency_qpl_marker_id': 36707139, 'is_from_aymh': 0, 'offline_experiment_group': offline_experiment_group, 'is_from_landing_page': 0, 'left_nav_button_action': 'NONE', 'password_text_input_id': f'{text_input_id}:82', 'is_from_empty_password': 0, 'is_from_msplit_fallback': 0, 'ar_event_source': 'login_home_page', 'qe_device_id': self.uuid, 'username_text_input_id': f'{text_input_id}:81', 'layered_homepage_experiment_group': 'Deploy: Not in Experiment', 'device_id': self.android_device_id, 'login_surface': 'login_home', 'INTERNAL__latency_qpl_instance_id': int(time.time() * 1000), 'reg_flow_source': 'login_home_native_integration_point', 'is_caa_perf_enabled': 1, 'credential_type': 'password', 'is_from_password_entry_page': 0, 'caller': 'gslr', 'family_device_id': self.phone_id, 'is_from_assistive_id': 0, 'access_flow_version': 'pre_mt_behavior', 'is_from_logged_in_switcher': 0}}
        attest_params = self.attestation_params()
        return self.bloks_async_action('com.bloks.www.bloks.caa.login.async.send_login_request', params, bloks_versioning_id=bloks_versioning_id, domain=domain, extra_headers={'X-IG-Attest-Params': attest_params} if attest_params else None, login=True)

    def bloks_caa_login(self, username: str='', password: Optional[str]=None, prepare: bool=True, domain: str=CAA_API_DOMAIN, verification_code: str='') -> Dict[str, Any]:
        """Run current Android CAA login, including its profile-code challenge."""
        username = username or self.username
        password = password or self.password
        if prepare and (not self.bloks_caa_login_prepare(username=username, domain=domain)):
            return {'logged_in': False, 'two_step_verification_context': '', 'result': {}, 'two_step': {}, 'reason': 'CAA preflight did not return account access and attestation data'}
        result = self.bloks_caa_login_send_request(password, username=username, domain=domain, auto_prepare=prepare)
        logged_in = self.bloks_apply_login_response(result)
        two_step = {}
        if not logged_in and self.bloks_caa_login_needs_two_step(result):
            two_step = self.bloks_caa_resolve_two_step_verification(result, verification_code=verification_code, domain=domain)
            logged_in = bool(two_step.get('logged_in'))
        context = '' if logged_in else self.bloks_extract_two_step_verification_context(result)
        return {'logged_in': logged_in, 'two_step_verification_context': context, 'result': result, 'two_step': two_step, 'reason': '' if logged_in else str(two_step.get('reason') or 'CAA login did not return a session')}

    def _bloks_collect_strings(self, value: Any, output: List[str]) -> None:
        if isinstance(value, str):
            output.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                self._bloks_collect_strings(child, output)
        elif isinstance(value, list):
            for child in value:
                self._bloks_collect_strings(child, output)

    def _bloks_all_text(self, result: Dict) -> str:
        strings: List[str] = []
        self._bloks_collect_strings(result, strings)
        return '\n'.join(strings)

    def bloks_caa_login_needs_two_step(self, result: Dict) -> bool:
        """Return whether CAA routed login to the profile-code challenge."""
        return AP_2SV_ENTRYPOINT in self._bloks_all_text(result)

    @staticmethod
    def _bloks_parenthesized_expression(value: str, start: int) -> str:
        """Return one balanced Bloks expression while respecting quoted strings."""
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(value)):
            character = value[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == '\\':
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == '(':
                depth += 1
            elif character == ')':
                depth -= 1
                if depth == 0:
                    return value[start:index + 1]
        return ''

    @classmethod
    def _bloks_expression_items(cls, value: str) -> List[str]:
        """Read direct operands without flattening nested expressions or scalars."""
        if not value.startswith('(') or not value.endswith(')'):
            return []
        items: List[str] = []
        decoder = json.JSONDecoder()
        index = 1
        while index < len(value) - 1:
            if value[index].isspace():
                index += 1
                continue
            if value[index] == '(':
                item = cls._bloks_parenthesized_expression(value, index)
                if not item:
                    return []
            elif value[index] == '"':
                try:
                    _, consumed = decoder.raw_decode(value[index:])
                except JSONDecodeError:
                    return []
                item = value[index:index + consumed]
            else:
                atom = re.match('[^()\\s"]+', value[index:])
                if not atom:
                    return []
                item = atom.group()
            items.append(item)
            index += len(item)
        return items

    def _bloks_extract_context_value(self, result: Dict, app_id: str, context_key: str) -> str:
        """Read a literal context from the target app's static parameter map."""
        strings: List[str] = []
        self._bloks_collect_strings(result, strings)
        anchor = re.compile(re.escape(json.dumps(app_id)))
        app_reference = re.compile('(?<![_a-zA-Z0-9])com\\.bloks\\.[_a-zA-Z0-9.]+')
        for text in strings:
            for found in anchor.finditer(text):
                map_start = text.find('(f4i', found.end())
                if map_start < 0:
                    continue
                next_app = app_reference.search(text, found.end())
                if next_app and next_app.start() < map_start:
                    continue
                expression = self._bloks_parenthesized_expression(text, map_start)
                if not expression:
                    continue
                while expression:
                    items = self._bloks_expression_items(expression)
                    if len(items) != 3 or items[0] != 'f4i':
                        break
                    keys = self._bloks_expression_items(items[1])
                    values = self._bloks_expression_items(items[2])
                    if keys[:1] != ['dkc'] or values[:1] != ['dkc'] or len(keys) != len(values):
                        break
                    expression = ''
                    for key, value in zip(keys[1:], values[1:]):
                        if not key.startswith('"'):
                            continue
                        name = json.loads(key)
                        if name == context_key:
                            return json.loads(value) if value.startswith('"') else ''
                        if name == 'server_params':
                            expression = value
        return ''

    def _find_bloks_value(self, data: Any, key: str) -> Any:
        if isinstance(data, dict):
            value = data.get(key)
            if value:
                return value
            for child in data.values():
                value = self._find_bloks_value(child, key)
                if value:
                    return value
        elif isinstance(data, list):
            for child in data:
                value = self._find_bloks_value(child, key)
                if value:
                    return value
        elif isinstance(data, str) and len(data) < 10000 and (key in data):
            try:
                value = self._find_bloks_value(json.loads(data), key)
            except (TypeError, ValueError):
                value = None
            if value:
                return value
        return None

    @staticmethod
    def _extract_first_json_string(value: str, start: int) -> str:
        quote_index = value.find('"', start)
        if quote_index < 0:
            return ''
        try:
            decoded, _ = json.JSONDecoder().raw_decode(value[quote_index:])
        except JSONDecodeError:
            return ''
        return decoded if isinstance(decoded, str) else ''

    def bloks_extract_two_step_verification_context(self, result: Dict) -> str:
        """
        Extract ``two_step_verification_context`` from a CAA/Bloks login result.

        Current app responses can place this context inside the Bloks action
        program that redirects to ``two_step_verification.entrypoint``. This
        helper first checks normal JSON containers, then parses that action
        parameter map without logging or returning any other sensitive fields.
        """
        value = self._find_bloks_value(result, 'two_step_verification_context')
        if isinstance(value, str) and value.strip():
            return value.strip()
        action = result.get('layout', {}).get('bloks_payload', {}).get('action', '')
        if not isinstance(action, str):
            return ''
        return self._bloks_extract_context_value({'action': action}, 'com.bloks.www.two_step_verification.entrypoint', 'two_step_verification_context').strip()

    def bloks_extract_login_response(self, result: Dict) -> Dict[str, Any]:
        """
        Extract an embedded login response from a Bloks action payload.

        Successful CAA/Bloks two-factor responses can place a JSON object inside
        ``layout.bloks_payload.action``. This helper returns the decoded
        ``login_response``, response ``headers``, cookie values, raw cookie
        header text, and raw embedded object. If no payload is found, an empty
        dictionary is returned.

        Returns
        -------
        Dict
            Parsed login payload or ``{}``.
        """
        action = result.get('layout', {}).get('bloks_payload', {}).get('action', '')
        if not isinstance(action, str):
            return {}
        decoder = json.JSONDecoder()
        index = 0
        while index < len(action):
            if action[index] != '"':
                index += 1
                continue
            try:
                value, end = decoder.raw_decode(action[index:])
            except JSONDecodeError:
                index += 1
                continue
            index += max(end, 1)
            if not isinstance(value, str) or 'login_response' not in value:
                continue
            try:
                raw = json.loads(value)
                login_response = json.loads(raw['login_response'])
                headers = json.loads(raw.get('headers') or '{}')
            except (KeyError, TypeError, ValueError):
                continue
            raw_cookies = raw.get('cookies') or ''
            cookies = SimpleCookie()
            cookies.load(raw_cookies.replace('Set-Cookie: ', ''))
            return {'login_response': login_response, 'headers': headers, 'cookies': {name: morsel.value for name, morsel in cookies.items()}, 'raw_cookies': raw_cookies, 'raw': raw}
        return {}

    def bloks_apply_login_response(self, result: Dict) -> bool:
        """
        Apply a parsed Bloks login response to the client session.

        ``result`` may be either the raw Bloks response returned by
        ``bloks_two_step_verification_verify_code(...)`` or the dictionary
        returned by :meth:`bloks_extract_login_response`.

        Returns
        -------
        bool
            ``True`` when authorization or session cookies were applied.
        """
        parsed = result if 'headers' in result or 'cookies' in result else self.bloks_extract_login_response(result)
        if not parsed:
            return False
        headers = parsed.get('headers') or {}
        cookies = parsed.get('cookies') or {}
        authorization = headers.get('IG-Set-Authorization') or headers.get('ig-set-authorization')
        if authorization:
            self.authorization_data = self.parse_authorization(authorization)
            if self.authorization:
                self.private.headers['Authorization'] = self.authorization
        for name, value in cookies.items():
            self.private.cookies.set(name, value)
        if cookies.get('sessionid'):
            self.public.cookies.set('sessionid', cookies['sessionid'])
        ig_u_rur = headers.get('ig-set-ig-u-rur') or headers.get('IG-Set-IG-U-RUR')
        if ig_u_rur:
            self.set_ig_u_rur(ig_u_rur)
            self.private.headers['IG-U-RUR'] = ig_u_rur
        ig_www_claim = headers.get('x-ig-set-www-claim') or headers.get('X-IG-Set-WWW-Claim')
        if ig_www_claim:
            self.set_ig_www_claim(ig_www_claim)
            self.private.headers['X-IG-WWW-Claim'] = ig_www_claim
        return bool(authorization or cookies.get('sessionid'))
