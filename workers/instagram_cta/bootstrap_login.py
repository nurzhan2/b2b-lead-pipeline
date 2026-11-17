"""One-off interactive login that creates ig_session.json.

Kept separate from main.py on purpose: the first login on a new account or a
new IP almost always triggers a challenge (a code sent to email/SMS), which
needs a human at a prompt. main.py assumes a valid session already exists and
must never block waiting for input - it runs unattended.

Run once, from the repo root:
    python -m workers.instagram_cta.bootstrap_login

Re-run it whenever main.py logs `ig_client.login_required`, which means the
session was invalidated (password change, logout everywhere, or a block).
"""

import sys

from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, UnknownError

from .config import Settings


def main() -> None:
    settings = Settings()
    client = Client(private_transport="curl")

    if settings.ig_proxy:
        client.set_proxy(settings.ig_proxy)
        print(f"proxy: {settings.ig_proxy.split('@')[-1]}")
    else:
        print("WARNING: no IG_PROXY set - logging in from your own IP")

    # instagrapi asks for the challenge/2FA code through this callback rather
    # than raising, so the whole flow stays in one interactive session.
    client.challenge_code_handler = lambda username, choice: input(
        f"code sent to {choice} for @{username}: "
    ).strip()
    client.change_password_handler = lambda username: input(
        f"new password for @{username}: "
    ).strip()

    try:
        client.login(settings.ig_username, settings.ig_password)
    except TwoFactorRequired:
        code = input("2FA code from your authenticator app: ").strip()
        client.login(settings.ig_username, settings.ig_password, verification_code=code)
    except ChallengeRequired as exc:
        print(f"challenge could not be resolved automatically: {exc}")
        print("Open Instagram on your phone, confirm it was you, wait an hour, retry.")
        sys.exit(1)
    except UnknownError as exc:
        message = str(exc)
        if "out of date" in message.lower() or "needs_upgrade" in message.lower():
            print("Instagram rejected the emulated app version.")
            print("Upgrade dependencies, then retry:")
            print(
                r".\.venv\Scripts\python -m pip install --upgrade "
                r'"instagrapi>=3.0.2,<4"'
            )
            sys.exit(1)
        raise

    client.dump_settings(settings.ig_session_file)
    me = client.account_info()
    print(f"logged in as @{me.username} - session saved to {settings.ig_session_file}")
    print("Do NOT run main.py today. Let the account sit for a day first.")


if __name__ == "__main__":
    main()
