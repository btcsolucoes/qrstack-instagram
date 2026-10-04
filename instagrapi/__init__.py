"""QrStack image-story-only fork. Experimental private API."""
import json
import logging
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from instagrapi.mixins.public import PublicRequestMixin
from instagrapi.mixins.private import PrivateRequestMixin
from instagrapi.mixins.graphql import PrivateGraphQLRequestMixin
from instagrapi.mixins.auth import LoginMixin
from instagrapi.mixins.attestation import DeviceAttestationMixin
from instagrapi.mixins.bloks import BloksMixin
from instagrapi.mixins.password import PasswordMixin
from instagrapi.mixins.photo import UploadPhotoMixin
from instagrapi.exceptions import ClientError, LoginRequired, ChallengeRequired
from instagrapi.types import StoryLink

__version__ = "0.1.0"

class Client(PublicRequestMixin, PrivateRequestMixin, PrivateGraphQLRequestMixin, LoginMixin,
             DeviceAttestationMixin, BloksMixin, PasswordMixin, UploadPhotoMixin):
    def __init__(self, settings=None):
        self.tls_verify = True
        self.request_timeout = 15
        self.session_retry_total = 0
        self.session_retry_statuses = []
        self.session_retry_backoff_factor = 0
        self.public_request_retries_count = 0
        self.public_request_retries_timeout = 0
        self.timezone_offset = -10800
        self.timezone_name = "America/Sao_Paulo"
        self.push_disabled = True
        super().__init__()
        self.settings = deepcopy(settings or {})
        self.settings.update(tls_verify=True, session_retry_total=0,
                             session_retry_statuses=[], public_request_retries_count=0)
        self.logger = logging.Logger("qrstack.silent")
        self.logger.addHandler(logging.NullHandler())
        self.private_request_logger = self.logger
        self.public_request_logger = self.logger
        self.request_logger = self.logger
        self.delay_range = None
        self.authorization_data = {}
        self.last_json = {}
        self.username = None
        self.password = None
        self.init()

    def private_request(self, endpoint, data=None, params=None, login=False,
                        with_signature=True, headers=None, extra_sig=None, domain=None):
        headers = dict(headers or {})
        if self.authorization:
            headers["Authorization"] = self.authorization
        self.private_requests_count += 1
        return self._send_private_request(endpoint, data=data, params=params,
            login=login, with_signature=with_signature, headers=headers,
            extra_sig=extra_sig, domain=domain)

    def request_log(self, response):
        pass

    def account_info(self):
        return self.private_request("accounts/current_user/", params={"edit": "true"})

    def login_flow(self):
        return True

    def login_legacy(self, *args, **kwargs):
        raise LoginRequired("Explicit reconnection required; legacy fallback disabled")

    def bloks_caa_resolve_two_step_verification(self, *args, **kwargs):
        raise ChallengeRequired("Human verification required")

    def _login_with_bloks_two_factor(self, *args, **kwargs):
        raise ChallengeRequired("Human verification required")

    def login(self, username=None, password=None, relogin=False, verification_code=""):
        if self.user_id:
            self.account_info()
            return True
        if relogin:
            raise LoginRequired("Automatic relogin disabled")
        try:
            return LoginMixin.login(self, username, password, verification_code=verification_code)
        finally:
            self.password = None

    def photo_upload_to_story(self, path, links=None):
        links = links or []
        if len(links) != 1 or urlparse(str(links[0].webUri)).scheme != "https":
            raise ValueError("Exactly one HTTPS StoryLink is required")
        link = links[0]
        if not (0 < link.width <= 1 and 0 < link.height <= 1 and
                link.width / 2 <= link.x <= 1 - link.width / 2 and
                link.height / 2 <= link.y <= 1 - link.height / 2):
            raise ValueError("Sticker must fit inside the story")
        self.private_request("media/validate_reel_url/", {
            "url": str(link.webUri), "_uid": str(self.user_id), "_uuid": self.uuid})
        upload_id, width, height = self.photo_rupload(Path(path), for_story=True, resize_mode="fit")
        sticker = dict(type="story_link", is_sticker=True, link_type="web",
            url=str(link.webUri), x=link.x, y=link.y, z=link.z,
            width=link.width, height=link.height, rotation=link.rotation,
            tap_state=0, tap_state_str_id="link_sticker_default")
        result = self.private_request("media/configure_to_story/", self.with_default_data({
            "upload_id": upload_id, "source_type": "4", "configure_mode": "1",
            "device": self.device, "device_id": self.android_device_id,
            "creation_surface": "camera", "capture_type": "normal",
            "extra": {"source_width": width, "source_height": height},
            "story_sticker_ids": "link_sticker_default", "tap_models": json.dumps([sticker]),
        }))
        if not result or not result.get("media", {}).get("pk"):
            raise ClientError("Publication outcome unknown; do not retry")
        return str(result["media"]["pk"])

    def get_story_status(self, story_id):
        result = self.private_request(f"feed/user/{self.user_id}/story/")
        items = (result.get("reel") or {}).get("items", [])
        return next((item for item in items if str(item.get("pk")) == str(story_id)), None)

    def close(self):
        self.password = None
        self.private.close()
        self.public.close()
        self.graphql.close()
