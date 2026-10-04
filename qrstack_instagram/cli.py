import argparse
import getpass
import os
from . import Publisher, PublicationStopped, Vault


def main():
    parser = argparse.ArgumentParser(description="QrStack internal test publisher")
    parser.add_argument("action", choices=["connect", "publish", "status", "stop"])
    parser.add_argument("account")
    parser.add_argument("--image")
    parser.add_argument("--url")
    parser.add_argument("--job")
    args = parser.parse_args()
    key = os.environ.get("QRSTACK_VAULT_KEY")
    if not key:
        parser.error("Set QRSTACK_VAULT_KEY externally; never commit it")
    vault = Vault(os.environ.get("QRSTACK_VAULT_PATH", ".local/vault.db"), key)
    publisher = Publisher(vault)
    try:
        if args.action == "connect":
            publisher.connect(args.account, getpass.getpass("Instagram password (not saved): "))
            print("CONNECTED")
        elif args.action == "publish":
            if not all([args.image, args.url, args.job]):
                parser.error("publish requires --image, --url and --job")
            print("PUBLISHED", publisher.publish(args.account, args.job, args.image, args.url))
        elif args.action == "stop":
            publisher.disable_account(args.account)
            print("FROZEN")
        else:
            print(vault.get(args.account)["state"])
    except Exception as error:
        # No traceback or exception message from third-party responses.
        print("STOPPED:", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        vault.close()
