import argparse
import getpass
import json
import os
from . import Publisher, PublicationStopped, Vault
from .publisher import normalize_account


def main():
    parser = argparse.ArgumentParser(description="QrStack internal test publisher")
    parser.add_argument("action", choices=["connect", "publish", "status", "verify", "stop"])
    parser.add_argument("account")
    parser.add_argument("--image")
    parser.add_argument("--url")
    parser.add_argument("--job")
    parser.add_argument("--fresh-login", action="store_true", help="Explicitly discard expired login credentials, keeping the device")
    args = parser.parse_args()
    args.account = normalize_account(args.account)
    if args.fresh_login and args.action != "connect":
        parser.error("--fresh-login is only valid with connect")
    if args.action == "publish" and not all([args.image, args.url, args.job]):
        parser.error("publish requires --image, --url and --job")
    key = os.environ.get("QRSTACK_VAULT_KEY")
    if not key:
        parser.error("Set QRSTACK_VAULT_KEY externally; never commit it")
    vault = None
    try:
        vault = Vault(os.environ.get("QRSTACK_VAULT_PATH", ".local/vault.db"), key)
        publisher = Publisher(vault)
        if args.action == "connect":
            publisher.gate(args.account)
            if vault.has_unresolved_job(args.account):
                raise PublicationStopped("Review unresolved publication before reconnecting")
            publisher.connect(args.account, getpass.getpass("Instagram password (not saved): "), fresh_login=args.fresh_login)
            print("CONNECTED")
        elif args.action == "publish":
            print("PUBLISHED", publisher.publish(args.account, args.job, args.image, args.url))
        elif args.action == "verify":
            print(json.dumps(publisher.verify(args.account, args.job)))
        elif args.action == "stop":
            publisher.disable_account(args.account)
            print("FROZEN")
        else:
            value = vault.get(args.account)
            result = {"state": value["state"], "source": "LOCAL"}
            if value.get("error_class"):
                result["error_class"] = value["error_class"]
            if args.job:
                result["job"] = vault.get_job(args.account, args.job)
            print(json.dumps(result))
    except Exception as error:
        # No traceback or exception message from third-party responses.
        print("STOPPED:", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        if vault is not None:
            vault.close()
