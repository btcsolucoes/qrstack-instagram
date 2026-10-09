import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from PIL import Image
from instagrapi import Client, StoryLink
from .vault import AccountChanged
from .pacing import PUBLISH_WINDOW_SECONDS, REQUEST_BUFFER_SECONDS, retry_after_seconds


class StoryPublisher(Protocol):
    def publish(self, account: str, job: str, media: str, target_url: str) -> str: ...


class PublicationStopped(RuntimeError):
    pass


class AccountIdentityMismatch(PublicationStopped):
    pass


def normalize_account(account):
    return account.strip().lstrip("@").lower()


def classify(error):
    name = type(error).__name__
    response = getattr(error, "response", None)
    if (getattr(response, "status_code", None) == 429
            or "Throttl" in name or "PleaseWait" in name or "RateLimit" in name):
        return "COOLDOWN"
    if "Suspend" in name or "Disabled" in name:
        return "SUSPENDED"
    if "Challenge" in name or "Checkpoint" in name or "Feedback" in name:
        return "FROZEN"
    if "Login" in name or "Password" in name or "TwoFactor" in name:
        return "RECONNECT_REQUIRED"
    return "FROZEN"


class Publisher:
    def __init__(self, vault, client_factory=Client, *, request_clock=None, sleep=None):
        self.vault = vault
        self.client_factory = client_factory
        self.request_clock = request_clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.approved_accounts = set()

    def gate(self, account):
        if os.environ.get("QRSTACK_ENABLE_PRIVATE_PUBLISHER") != "1":
            raise PublicationStopped("Private publisher is disabled")
        allowed = [normalize_account(item) for item in os.environ.get("QRSTACK_TEST_ACCOUNTS", "").split(",")]
        if (account not in allowed and account not in self.approved_accounts) or not account:
            raise PublicationStopped("Account is not on the internal test allowlist")

    def freeze(self, account, error, revision):
        state = classify(error)
        update = {"state": state, "error_class": type(error).__name__}
        if state == "COOLDOWN":
            headers = getattr(getattr(error, "response", None), "headers", {})
            update["retry_not_before"] = time.time() + max(PUBLISH_WINDOW_SECONDS, retry_after_seconds(headers))
        try:
            self.vault.put(account, update,
                           expected_revision=revision, merge=True)
        except AccountChanged:
            pass  # In particular, never overwrite a concurrent stop.
        # Deliberately allowlist fields, never log upstream messages/tracebacks.
        logging.getLogger("qrstack.audit").warning("publisher_stopped error_class=%s", type(error).__name__)

    def check_identity(self, client, account, value):
        response = client.account_info()
        user = response.get("user") if isinstance(response, dict) else None
        if not isinstance(user, dict):
            raise AccountIdentityMismatch("Current account identity unavailable")
        user_id = str(user.get("pk") or "")
        if (normalize_account(str(user.get("username") or "")) != account
                or not user_id.isdecimal() or int(user_id) <= 0
                or user_id != str(client.user_id)
                or (value.get("user_id") and user_id != value["user_id"])):
            raise AccountIdentityMismatch("Authenticated account does not match the internal account")
        return user_id

    def check_active(self, account, revision):
        self.gate(account)
        value = self.vault.get(account)
        if value["state"] != "HEALTHY" or value.get("revision", 0) != revision:
            raise PublicationStopped("Account stopped or changed during publication")

    def connect(self, account, password, *, fresh_login=False, expected_user_id=None):
        account = normalize_account(account)
        self.gate(account)
        previous = self.vault.get(account)
        if previous.get("retry_not_before", 0) > time.time():
            raise PublicationStopped("Instagram requested a pause; wait and review before reconnecting")
        if self.vault.has_unresolved_job(account):
            raise PublicationStopped("Review unresolved publication before reconnecting")
        revision = previous.get("revision", 0)
        client = None
        try:
            client = self.client_factory(settings=previous.get("settings"))
            if fresh_login:
                client.clear_login_session()
            client.login(account, password)
            user_id = self.check_identity(client, account, previous)
            if expected_user_id is not None and user_id != str(expected_user_id):
                raise AccountIdentityMismatch("Connected account differs from the requested binding")
            self.gate(account)
            self.vault.put(account, {"state": "HEALTHY", "settings": client.get_settings(), "user_id": user_id,
                                    "verified_at": datetime.now(timezone.utc).isoformat()},
                           expected_revision=revision)
        except Exception as error:
            self.freeze(account, error, revision)
            raise PublicationStopped(type(error).__name__) from None
        finally:
            if client is not None:
                client.close()

    def publish(self, account, job, media, target_url, *, before_request=None, expected_user_id=None):
        account = normalize_account(account)
        self.gate(account)
        value = self.vault.get(account)
        if value["state"] != "HEALTHY" or not value.get("settings"):
            raise PublicationStopped("Account requires explicit reconnection or review")
        if expected_user_id is not None and str(value.get("user_id") or "") != str(expected_user_id):
            raise AccountIdentityMismatch("Platform identity does not match the connected account")
        if not job or not Path(media).is_file():
            raise ValueError("A unique job ID and local image are required")
        link = StoryLink(webUri=target_url, x=0.5, y=0.72, width=0.56, height=0.10)
        if not target_url.startswith("https://"):
            raise ValueError("HTTPS target required")
        if Path(media).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("JPG/JPEG/PNG/WEBP image required")
        with Image.open(media) as image:
            if image.size != (1080, 1920):
                raise ValueError("Internal test image must be 1080x1920")
            image.verify()
        revision = value.get("revision", 0)
        self.vault.reserve(account, job)
        client = None
        confirmed = False
        try:
            last_request = None

            def guard():
                nonlocal last_request
                self.check_active(account, revision)
                if last_request is not None:
                    remaining = REQUEST_BUFFER_SECONDS - (self.request_clock() - last_request)
                    if remaining > 0:
                        self.sleep(remaining)
                # Pauses and revoked permissions are rechecked AFTER the buffer.
                self.check_active(account, revision)
                if before_request is not None:
                    before_request()
                last_request = self.request_clock()

            guard()
            client = self.client_factory(settings=value["settings"])
            self.check_identity(client, account, value)
            self.check_active(account, revision)
            if before_request is not None:
                before_request()
            story = client.photo_upload_to_story(media, links=[link],
                before_request=guard)
            self.vault.finish(account, job, "PUBLISHED", story)
            confirmed = True
            try:
                self.vault.put(account, {"settings": client.get_settings()},
                               expected_revision=revision, merge=True)
            except AccountChanged:
                pass  # Keep the published result and the concurrent stop.
            return story
        except Exception as error:
            if not confirmed:
                self.vault.finish(account, job, "UNKNOWN")
            self.freeze(account, error, revision)
            raise PublicationStopped(type(error).__name__) from None
        finally:
            if client is not None:
                client.close()

    def verify(self, account, job=None):
        """Explicit remote read; never logs in, publishes or reconciles a job."""
        account = normalize_account(account)
        self.gate(account)
        value = self.vault.get(account)
        if not value.get("settings"):
            raise PublicationStopped("Explicit connection required")
        if value.get("retry_not_before", 0) > time.time():
            raise PublicationStopped("Instagram requested a pause; remote verification is deferred")
        record = self.vault.get_job(account, job) if job else None
        if job and (not record or not record["story"]):
            raise ValueError("Job has no confirmed Story ID; inspect Instagram manually")
        revision = value.get("revision", 0)
        client = None
        try:
            client = self.client_factory(settings=value["settings"])
            self.check_identity(client, account, value)
            result = {"account": account, "session": "VALID", "state": value["state"]}
            if record:
                result.update(job=job, publication=record["status"],
                              story="PRESENT" if client.get_story_status(record["story"]) is not None else "NOT_FOUND")
            self.vault.put(account, {"settings": client.get_settings()}, expected_revision=revision, merge=True)
            return result
        except Exception as error:
            self.freeze(account, error, revision)
            raise PublicationStopped(type(error).__name__) from None
        finally:
            if client is not None:
                client.close()

    def disable_account(self, account):
        self.vault.put(normalize_account(account), {"state": "FROZEN"}, merge=True)
