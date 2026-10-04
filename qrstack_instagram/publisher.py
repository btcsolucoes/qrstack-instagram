import logging
import os
from pathlib import Path
from typing import Protocol
from instagrapi import Client, StoryLink


class StoryPublisher(Protocol):
    def publish(self, account: str, job: str, media: str, target_url: str) -> str: ...


class PublicationStopped(RuntimeError):
    pass


def classify(error):
    name = type(error).__name__
    if "Suspend" in name or "Disabled" in name:
        return "SUSPENDED"
    if "Challenge" in name or "Checkpoint" in name or "Feedback" in name:
        return "FROZEN"
    if "Login" in name or "Password" in name or "TwoFactor" in name:
        return "RECONNECT_REQUIRED"
    if "Throttl" in name or "PleaseWait" in name:
        return "COOLDOWN"
    return "FROZEN"


class Publisher:
    def __init__(self, vault, client_factory=Client):
        self.vault = vault
        self.client_factory = client_factory

    def gate(self, account):
        if os.environ.get("QRSTACK_ENABLE_PRIVATE_PUBLISHER") != "1":
            raise PublicationStopped("Private publisher is disabled")
        allowed = os.environ.get("QRSTACK_TEST_ACCOUNTS", "").split(",")
        if account not in allowed or not account:
            raise PublicationStopped("Account is not on the internal test allowlist")

    def freeze(self, account, error):
        value = self.vault.get(account)
        value.update(state=classify(error), error_class=type(error).__name__)
        self.vault.put(account, value)
        # Deliberately allowlist fields, never log upstream messages/tracebacks.
        logging.getLogger("qrstack.audit").warning("publisher_stopped error_class=%s", type(error).__name__)

    def connect(self, account, password):
        self.gate(account)
        previous = self.vault.get(account)
        client = self.client_factory(settings=previous.get("settings"))
        try:
            client.login(account, password)
            self.vault.put(account, {"state": "HEALTHY", "settings": client.get_settings()})
        except Exception as error:
            self.freeze(account, error)
            raise PublicationStopped(type(error).__name__) from None
        finally:
            client.close()

    def publish(self, account, job, media, target_url):
        self.gate(account)
        value = self.vault.get(account)
        if value["state"] != "HEALTHY" or not value.get("settings"):
            raise PublicationStopped("Account requires explicit reconnection or review")
        if not job or not Path(media).is_file():
            raise ValueError("A unique job ID and local image are required")
        link = StoryLink(webUri=target_url, x=0.5, y=0.72, width=0.56, height=0.10)
        if not target_url.startswith("https://"):
            raise ValueError("HTTPS target required")
        self.vault.reserve(account, job)
        client = None
        try:
            client = self.client_factory(settings=value["settings"])
            client.account_info()
            self.gate(account)
            story = client.photo_upload_to_story(media, links=[link])
            self.vault.put(account, {**value, "settings": client.get_settings()})
            self.vault.finish(account, job, "PUBLISHED", story)
            return story
        except Exception as error:
            self.vault.finish(account, job, "UNKNOWN")
            self.freeze(account, error)
            raise PublicationStopped(type(error).__name__) from None
        finally:
            if client:
                client.close()

    def disable_account(self, account):
        self.vault.put(account, {**self.vault.get(account), "state": "FROZEN"})
