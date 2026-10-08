"""Pull bridge for the QrStack queue. No credentials, login or publication retries."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit

from PIL import Image
import requests

from .publisher import Publisher, PublicationStopped, normalize_account
from .vault import Vault
from .pacing import retry_after_seconds


MAX_MEDIA_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
VERSION = "0.1.0"


class PlatformStopped(PublicationStopped):
    pass


class PlatformUnavailable(PlatformStopped):
    """Safe transport signal; the runner journal decides what may be retried."""
    def __init__(self, retry_after=0):
        self.retry_after_seconds = retry_after
        super().__init__("Platform temporarily unavailable")


def https_origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise PlatformStopped("HTTPS URL without credentials or fragment required")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or 443


def load_account_map(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or not value:
        raise PlatformStopped("Explicit restaurant-to-account map required")
    result = {}
    for slug, account in value.items():
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", slug)
                or not isinstance(account, str)
                or not re.fullmatch(r"[a-z0-9._]{1,30}", normalize_account(account))):
            raise PlatformStopped("Invalid account map")
        result[slug] = normalize_account(account)
    if len(set(result.values())) != len(result):
        raise PlatformStopped("Each restaurant must have a separate Instagram account")
    return result


class PlatformClient:
    def __init__(self, base_url, publisher_id, token, *, session=None):
        self.origin = https_origin(base_url)
        if urlsplit(base_url).query:
            raise PlatformStopped("Worker URL must not contain query parameters")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", publisher_id):
            raise PlatformStopped("Invalid publisher ID")
        if not token or any(char in token for char in "\r\n"):
            raise PlatformStopped("Publisher token required")
        self.base_url = base_url
        self.publisher_id = publisher_id
        self.token = token
        self.session = session or requests.Session()
        # Avoid implicit netrc credentials and environment proxies changing auth.
        self.session.trust_env = False
        # Host case, the default HTTPS port and a trailing slash must not create a
        # second local publication namespace for the same remote queue.
        self.scope = hashlib.sha256(json.dumps([self.origin, publisher_id]).encode()).hexdigest()

    def _request(self, method, url, *, claim_token=None, **kwargs):
        if https_origin(url) != self.origin:
            raise PlatformStopped("Refusing to send publisher credentials to another origin")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if claim_token:
            if any(char in claim_token for char in "\r\n"):
                raise PlatformStopped("Invalid claim token")
            headers["X-Claim-Token"] = claim_token
        try:
            response = self.session.request(method, url, headers=headers, timeout=(5, 30),
                                            allow_redirects=False, stream=True, **kwargs)
        except requests.exceptions.SSLError:
            raise PlatformStopped("Platform TLS verification failed") from None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            raise PlatformUnavailable() from None
        if not 200 <= response.status_code < 300:
            retry_after = retry_after_seconds(response.headers)
            response.close()
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise PlatformUnavailable(retry_after)
            # Never include upstream bodies, URLs, tokens or exception messages.
            raise PlatformStopped("Platform request rejected")
        return response

    @staticmethod
    def _bounded_body(response, maximum):
        body = bytearray()
        try:
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdecimal() or int(declared) > maximum):
                raise PlatformStopped("Response exceeds size limit")
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > maximum:
                    raise PlatformStopped("Response exceeds size limit")
            return bytes(body)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError):
            raise PlatformUnavailable() from None
        finally:
            response.close()

    def call(self, action, *, method="GET", job=None, data=None):
        params = {"action": action}
        if method == "GET":
            params["publisher_id"] = self.publisher_id
            if job:
                params["job_id"] = job["id"]
        body = None if method == "GET" else {"publisher_id": self.publisher_id, **(data or {})}
        response = self._request(method, self.base_url, params=params, json=body,
                                 claim_token=job.get("claim_token") if job else None)
        payload = json.loads(self._bounded_body(response, MAX_JSON_BYTES))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise PlatformStopped("Invalid platform response")
        return payload

    def next_job(self):
        return self.call("getNextInstagramStoryJob")

    def inspect(self, job):
        return self.call("getInstagramPublisherJob", job=job)

    def update(self, job, status, checkpoint, *, media_id=None, error_code=None):
        data = {"job_id": job["id"], "claim_token": job["claim_token"],
                "status": status, "checkpoint": checkpoint}
        if media_id is not None:
            data["media_id"] = str(media_id)
        if error_code:
            data["error_code"] = error_code
        return self.call("updateInstagramStoryJob", method="POST", job=job, data=data)

    def download(self, job, directory):
        response = self._request("GET", job["media_url"], claim_token=job["claim_token"])
        content = self._bounded_body(response, MAX_MEDIA_BYTES)
        if hashlib.sha256(content).hexdigest() != job["media_sha256"]:
            raise PlatformStopped("Media hash mismatch")
        path = Path(directory) / "story.bin"
        path.write_bytes(content)
        with Image.open(path) as decoded:
            if decoded.size != (1080, 1920) or decoded.format not in {"JPEG", "PNG", "WEBP"}:
                raise PlatformStopped("A 1080x1920 JPG/PNG/WEBP is required")
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[decoded.format]
            decoded.verify()
        final = path.with_suffix(suffix)
        path.rename(final)
        return final

    def close(self):
        self.session.close()


@contextmanager
def runner_lock(path):
    """An OS lock is released on process death, unlike a persistent lease."""
    with open(str(path) + ".platform.lock", "a+b") as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PlatformStopped("Another runner is already using this vault") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class PlatformRunner:
    def __init__(self, api, publisher, accounts):
        self.api = api
        self.publisher = publisher
        self.vault = publisher.vault
        self.accounts = accounts

    def local_id(self, job):
        return "platform:" + hashlib.sha256((self.api.scope + "\0" + str(job["id"])).encode()).hexdigest()

    def validate_binding(self, job, *, require_healthy=True):
        if not isinstance(job, dict):
            raise PlatformStopped("Invalid job")
        account = self.accounts.get(job.get("restaurant_slug"))
        if not account or job.get("instagram_username") != account:
            raise PlatformStopped("Job account is outside the local restaurant map")
        user_id = str(job.get("instagram_user_id") or "")
        value = self.vault.get(account)
        if (not user_id.isdecimal() or int(user_id) <= 0
                or str(value.get("user_id") or "") != user_id):
            raise PlatformStopped("Job identity differs from the local connected account")
        self.publisher.gate(account)
        if require_healthy and (value["state"] != "HEALTHY" or not value.get("settings")):
            raise PlatformStopped("Explicit local reconnection or review required")
        if not isinstance(job.get("id"), (str, int)) or not str(job["id"]):
            raise PlatformStopped("Missing job ID")
        if not isinstance(job.get("claim_token"), str) or not job["claim_token"]:
            raise PlatformStopped("Missing claim token")
        return account

    def guard(self, original):
        self.validate_binding(original)
        response = self.api.inspect(original)
        job = response.get("job")
        if (response.get("can_publish") is not True
                or response.get("publishing", {}).get("enabled") is not True
                or not isinstance(job, dict) or job.get("status") != "publishing"):
            raise PlatformStopped("Platform publication has been stopped")
        # Snapshot binding and content must not change after the claim.
        for field in ("id", "restaurant_slug", "instagram_username", "instagram_user_id",
                      "story_link", "media_sha256"):
            if str(job.get(field)) != str(original.get(field)):
                raise PlatformStopped("Platform job changed during publication")

    def _ack(self, delivery, status, *, media_id=None, error_code=None):
        # Persist desired acknowledgement BEFORE the request; failure can only retry ACK.
        delivery.update(ack_status=status, media_id=media_id, error_code=error_code)
        self.vault.save_platform_delivery(delivery)
        self.api.update(delivery["job"], status, "runner_" + status,
                        media_id=media_id, error_code=error_code)
        delivery["acked"] = True
        self.vault.save_platform_delivery(delivery)
        return {"status": status, "job_id": delivery["job"]["id"], "media_id": media_id}

    def _recover(self, delivery):
        job = delivery["job"]
        response = self.api.inspect(job)
        remote = response.get("job")
        if not isinstance(remote, dict):
            raise PlatformStopped("Recovery job unavailable")
        for field in ("id", "restaurant_slug", "instagram_username", "instagram_user_id",
                      "story_link", "media_sha256"):
            if str(remote.get(field)) != str(job.get(field)):
                raise PlatformStopped("Recovery job binding changed")
        # Recovery never loads an Instagram client or starts publication. Keep using
        # the encrypted original account binding even if local maps were removed.
        record = self.vault.get_job(delivery["account"], delivery["id"]) if delivery.get("account") else None
        if record and record["status"] == "PUBLISHED" and record.get("story"):
            return self._ack(delivery, "completed", media_id=record["story"])
        if delivery.get("ack_status"):
            return self._ack(delivery, delivery["ack_status"], media_id=delivery.get("media_id"),
                             error_code=delivery.get("error_code"))
        if record or delivery.get("permission_requested"):
            if delivery.get("account"):
                self.publisher.disable_account(delivery["account"])
            return self._ack(delivery, "outcome_unknown", error_code="runner_interrupted")
        # A crash before requesting publishing permission cannot have posted a Story.
        return self._ack(delivery, "failed_attention", error_code="runner_interrupted_before_publish")

    def run_once(self):
        with runner_lock(self.vault.path):
            pending = self.vault.platform_deliveries(self.api.scope)
            if pending:
                return self._recover(pending[0])
            response = self.api.next_job()
            job = response.get("job")
            if job is None:
                delay = response.get("poll_after_seconds", 30)
                return {"status": "idle", "poll_after_seconds": delay if isinstance(delay, int) and 15 <= delay <= 3600 else 30}
            # Retain enough authenticated queue context to report a rejected binding,
            # even if no local account may be associated with this restaurant.
            if (not isinstance(job, dict) or not isinstance(job.get("id"), (str, int))
                    or not str(job["id"]) or not isinstance(job.get("claim_token"), str)
                    or not job["claim_token"]):
                raise PlatformStopped("Queue returned a job without an ID or claim")
            binding_error = False
            try:
                account = self.validate_binding(job, require_healthy=False)
            except PlatformStopped:
                account = None
                binding_error = True
            delivery = {"id": self.local_id(job), "scope": self.api.scope,
                        "account": account, "job": job, "acked": False}
            try:
                self.vault.save_platform_delivery(delivery, new=True)
            except Exception:
                # Unique ID conflict is a hard stop, never a new ID or reservation.
                raise PlatformStopped("Local job already recorded; do not republish") from None
            if binding_error:
                status = "outcome_unknown" if job.get("status") in {"publishing", "outcome_unknown"} else "failed_attention"
                return self._ack(delivery, status, error_code="local_binding_rejected")
            if self.vault.get_job(account, delivery["id"]):
                return self._recover(delivery)
            if job.get("status") not in {"claimed", "preparing"}:
                delivery["permission_requested"] = True
                self.vault.save_platform_delivery(delivery)
                return self._recover(delivery)
            try:
                self.validate_binding(job)
                if self.vault.has_unresolved_job(account):
                    raise PlatformStopped("Account has an unresolved publication")
                if self.vault.publication_delay(account):
                    return self._ack(delivery, "failed_attention", error_code="local_publication_cooldown")
                https_origin(job["story_link"])
                if not re.fullmatch(r"[0-9a-f]{64}", job.get("media_sha256", "")):
                    raise PlatformStopped("Invalid media hash")
                self.api.update(job, "preparing", "runner_validate_media")
                with tempfile.TemporaryDirectory(prefix="qrstack-story-") as directory:
                    media = self.api.download(job, directory)
                    # Persist intent before the one-time permit. A lost response is
                    # uncertain and is never followed by another permit request.
                    delivery["permission_requested"] = True
                    self.vault.save_platform_delivery(delivery)
                    self.api.update(job, "publishing", "runner_before_instagram")
                    self.publisher.publish(account, delivery["id"], media, job["story_link"],
                                           expected_user_id=str(job["instagram_user_id"]),
                                           before_request=lambda: self.guard(job))
            except Exception:
                record = self.vault.get_job(account, delivery["id"])
                if record and record["status"] == "PUBLISHED" and record.get("story"):
                    return self._ack(delivery, "completed", media_id=record["story"])
                if delivery.get("permission_requested"):
                    self.publisher.disable_account(account)
                    return self._ack(delivery, "outcome_unknown", error_code="publisher_stopped")
                return self._ack(delivery, "failed_attention", error_code="runner_preflight_failed")
            return self._recover(delivery)


def run_loop(runner, *, loop=False, poll_seconds=30, sleep=time.sleep, emit=print):
    """Only retry the queue poll or journal recovery, never an Instagram request.

    A lost queue claim is recovered by its same publisher/claim, and any lost
    publishing permit has already been journaled as uncertain by run_once.
    """
    failures = 0
    while True:
        try:
            result = runner.run_once()
        except PlatformUnavailable as error:
            failures += 1
            # Six consecutive failures require attention. Capped exponential
            # backoff is a floor; a longer server Retry-After is never reduced.
            if not loop or failures >= 6:
                raise
            delay = max(min(900, 30 * (2 ** (failures - 1))), error.retry_after_seconds)
            emit(json.dumps({"status": "waiting_for_platform", "retry_after_seconds": delay}), flush=True)
            sleep(delay)
            continue
        failures = 0
        emit(json.dumps(result), flush=True)
        if not loop or result["status"] in {"outcome_unknown", "failed_attention"}:
            break
        sleep(max(poll_seconds, result.get("poll_after_seconds", 0)))


def main():
    parser = argparse.ArgumentParser(description="QrStack platform publisher bridge; credentials only through environment")
    sub = parser.add_subparsers(dest="action", required=True)
    register = sub.add_parser("register", help="Register publisher using QRSTACK_PLATFORM_OWNER_KEY")
    register.add_argument("--label", default="QrStack private publisher")
    bind = sub.add_parser("bind", help="Bind a restaurant to its explicitly connected local account")
    bind.add_argument("--slug", required=True)
    bind.add_argument("--disabled", action="store_true")
    sub.add_parser("check", help="Check local configuration and sessions, without network access")
    run = sub.add_parser("run", help="Consume one job by default; never log in automatically")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--loop", action="store_true")
    run.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    api = vault = None
    try:
        api = PlatformClient(os.environ["QRSTACK_PLATFORM_URL"], os.environ["QRSTACK_PUBLISHER_ID"],
                             os.environ["QRSTACK_PUBLISHER_TOKEN"])
        if args.action == "register":
            api.call("registerInstagramPublisher", method="POST", data={
                "publisher_token": api.token, "label": args.label, "version": VERSION,
                "owner_key": os.environ["QRSTACK_PLATFORM_OWNER_KEY"]})
            print(json.dumps({"status": "registered"}))
            return
        accounts = load_account_map(os.environ["QRSTACK_PLATFORM_ACCOUNT_MAP"])
        vault = Vault(os.environ.get("QRSTACK_VAULT_PATH", ".local/vault.db"), os.environ["QRSTACK_VAULT_KEY"])
        publisher = Publisher(vault)
        if args.action == "bind":
            account = accounts[args.slug]
            publisher.gate(account)
            value = vault.get(account)
            if not str(value.get("user_id") or "").isdecimal():
                raise PlatformStopped("Connect and verify the intended local account first")
            api.call("bindInstagramAccount", method="POST", data={
                "slug": args.slug, "instagram_username": account, "instagram_user_id": str(value["user_id"]),
                "enabled": not args.disabled, "owner_key": os.environ["QRSTACK_PLATFORM_OWNER_KEY"]})
            print(json.dumps({"status": "bound", "slug": args.slug, "account": account, "enabled": not args.disabled}))
        elif args.action == "check":
            for account in accounts.values():
                publisher.gate(account)
                value = vault.get(account)
                if (value["state"] != "HEALTHY" or not value.get("settings")
                        or not str(value.get("user_id") or "").isdecimal() or vault.has_unresolved_job(account)):
                    raise PlatformStopped("Account requires explicit local connection or review")
            print(json.dumps({"status": "configuration_valid", "source": "LOCAL", "restaurants": sorted(accounts)}))
        else:
            if args.poll_seconds < 15:
                raise PlatformStopped("Poll interval must be at least 15 seconds")
            runner = PlatformRunner(api, publisher, accounts)
            run_loop(runner, loop=args.loop, poll_seconds=args.poll_seconds)
    except (Exception, KeyboardInterrupt) as error:
        print("STOPPED:", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        if api is not None:
            api.close()
        if vault is not None:
            vault.close()


if __name__ == "__main__":
    main()
