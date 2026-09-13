#!/usr/bin/env python3
"""Log the Telegram MCP server in, once per account. Run it yourself in a terminal: it asks for codes and secrets.

    python login.py me              your own account: api_id and api_hash from my.telegram.org, then phone, code,
                                    password
    python login.py bot             a bot: its token from @BotFather
    python login.py anthropic       a Claude API key, for the bot's answers to the people on its list
    python login.py claude          optional: a Claude Code token of the bot's own, for its answers to you on your
                                    subscription
    python login.py status          what is logged in, and whether responder.py runs
    python login.py logout me       end the session on Telegram's side and delete it here
    python login.py logout bot|anthropic|claude   forget that token or key
"""

import asyncio
import getpass
import sys

from telethon import TelegramClient, errors, utils
from telethon.sessions import StringSession

import common


def describe_user(user):
    name = utils.get_display_name(user)
    return f"{name} (@{user.username})" if user.username else name


def saved_client(saved):
    return TelegramClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                          receive_updates=False, **common.DEVICE)


def run_blocking(coroutine):
    """Run a coroutine that stops for input(). Unlike asyncio.run this sets no Ctrl+C handler of its own, so Ctrl+C
    stops a prompt at once instead of waiting for the next await."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def enter_code(client, phone):
    sent = await client.send_code_request(phone)
    print("Telegram has sent a login code to your Telegram app, into the chat called 'Telegram'. It is the")
    print("5-digit 'Login code' ('Код для входа в Telegram'), not the letters my.telegram.org sent for the site.")
    while True:
        code = input("Login code: ").strip()
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            return
        except errors.PhoneCodeInvalidError:
            print("That is not the code. Take the latest 'Login code' message and try again.")
        except errors.PhoneCodeExpiredError:
            sent = await client.send_code_request(phone)
            print("That code has expired, Telegram has sent a new one.")
        except errors.SessionPasswordNeededError:
            await enter_password(client)
            return


async def enter_password(client):
    print("The account has two-step verification, so now its cloud password. What you type is not shown:")
    print("switch the keyboard to the layout the password is in and check Caps Lock first.")
    while True:
        try:
            await client.sign_in(password=getpass.getpass("Please enter your secret password: "))
            return
        except errors.PasswordHashInvalidError:
            print("Telegram says the password is wrong. Try again - the code is not needed again - or Ctrl+C to stop.")


def login_me():
    saved = common.load_json(common.ME_FILE) or {}
    if saved.get("session"):
        print("The personal account is already logged in: 'login.py status' shows who, 'login.py logout me' ends it.")
        return
    if saved.get("api_id") and saved.get("api_hash"):
        api_id, api_hash = saved["api_id"], saved["api_hash"]
        print(f"Using api_id {api_id} from the earlier attempt (delete {common.ME_FILE} to enter another).")
    else:
        print("Personal account. Open https://my.telegram.org, log in, choose 'API development tools',")
        print("create an application (any name will do) and copy api_id and api_hash from there.")
        answer = input("api_id: ").strip()
        if not answer.isdigit():
            raise SystemExit("api_id is a number")
        api_id = int(answer)
        api_hash = getpass.getpass("api_hash (not shown): ").strip()
        common.save_json(common.ME_FILE, {"api_id": api_id, "api_hash": api_hash})

    async def run():
        client = TelegramClient(StringSession(), api_id, api_hash, receive_updates=False, **common.DEVICE)
        await client.connect()
        try:
            await enter_code(client, input("Phone number with the country code, like +79991234567: ").strip())
            return await client.get_me(), client.session.save()
        finally:
            await client.disconnect()

    try:
        me, session = run_blocking(run())
    except errors.ApiIdInvalidError:
        common.ME_FILE.unlink(missing_ok=True)
        raise SystemExit("Telegram does not accept this api_id and api_hash: run 'login.py me' again and copy both "
                         "from my.telegram.org.") from None
    except errors.PhoneNumberInvalidError:
        raise SystemExit("Telegram does not know that phone number: enter it with + and the country code.") from None
    except errors.FloodWaitError as error:
        raise SystemExit(f"Too many attempts: Telegram asks to wait {error.seconds} seconds before the next one.") from None
    except KeyboardInterrupt:
        raise SystemExit("Stopped.") from None
    common.save_json(common.ME_FILE, {"api_id": api_id, "api_hash": api_hash, "session": session})
    print(f"Logged in as {describe_user(me)}. Saved to {common.ME_FILE}.")
    print("That file is the whole account: keep it private. 'login.py logout me' ends the session.")


def login_bot():
    print("Bot. In Telegram open @BotFather, send /newbot and copy the token it gives you.")
    token = getpass.getpass("bot token (not shown): ").strip()
    try:
        me = common.bot_request(token, "getMe")
    except common.BotApiError as error:
        raise SystemExit(f"Telegram did not accept the token: {error.description}")
    common.save_json(common.BOT_FILE, {"token": token})
    print(f"Bot @{me['username']} saved to {common.BOT_FILE}.")
    print("A bot writes only to people who pressed Start in it and to groups it was added to.")


def login_anthropic():
    import anthropic

    print("A Claude API key, for the answers the bot writes by itself. Create one at https://platform.claude.com")
    print("under API keys. The API is billed by use, separately from a Claude subscription.")
    key = getpass.getpass("API key (not shown): ").strip()
    model = common.load_access()["settings"]["model"]
    try:
        anthropic.Anthropic(api_key=key).models.retrieve(model)
    except anthropic.AuthenticationError:
        raise SystemExit("The Claude API does not accept this key.") from None
    except anthropic.NotFoundError:
        print(f"The key works, but {model} is not available to its organization: pick another model in "
              f"{common.ACCESS_FILE} under settings.")
    except anthropic.APIStatusError as error:
        raise SystemExit(f"The Claude API answered {error.status_code}: {error.message}") from None
    except anthropic.APIConnectionError:
        raise SystemExit("Cannot reach the Claude API.") from None
    common.save_json(common.ANTHROPIC_FILE, {"api_key": key})
    print(f"Saved to {common.ANTHROPIC_FILE}. responder.py starts the bot's answers.")


def login_claude():
    from responder import claude_code

    print("Optional: a Claude Code token of the bot's own, for its answers to you on your Claude subscription.")
    print("Without one the bot uses this computer's Claude Code login, which brings your CLAUDE.md along.")
    executable = claude_code()
    if executable is None:
        raise SystemExit("Claude Code is not found on this computer: install it, or the Claude desktop app, first.")
    print("Get one in another PowerShell window - it opens the browser to sign in, then prints the token:")
    print(f'    & "{executable}" setup-token')
    token = getpass.getpass("token (not shown): ").strip()
    if not token:
        raise SystemExit("No token entered.")
    common.save_json(common.CLAUDE_TOKEN_FILE, {"token": token})
    print(f"Saved to {common.CLAUDE_TOKEN_FILE}; the bot uses it from your next question.")


def status():
    saved = common.load_json(common.ME_FILE) or {}
    if saved.get("session"):
        async def run():
            client = saved_client(saved)
            await client.connect()
            try:
                return await client.get_me() if await client.is_user_authorized() else None
            finally:
                await client.disconnect()

        user = asyncio.run(run())
        print(f"me: {describe_user(user)}" if user else "me: the saved session is no longer valid, run 'login.py me'")
    elif saved:
        print("me: api_id saved, not logged in yet - run 'login.py me'")
    else:
        print("me: not logged in")

    bot = common.load_json(common.BOT_FILE)
    if bot:
        try:
            print(f"bot: @{common.bot_request(bot['token'], 'getMe')['username']}")
        except common.BotApiError as error:
            print(f"bot: {error.description}")
    else:
        print("bot: not logged in")

    print("claude api: key saved" if common.ANTHROPIC_FILE.exists() else "claude api: no key - run 'login.py anthropic'")
    print("claude code: a token of the bot's own" if common.CLAUDE_TOKEN_FILE.exists() else
          "claude code: this computer's Claude Code login")
    print("responder: running" if common.responder_alive() else "responder: not running")


def logout(account):
    forgotten = {"bot": (common.BOT_FILE, "bot: token forgotten"),
                 "anthropic": (common.ANTHROPIC_FILE, "claude api: key forgotten"),
                 "claude": (common.CLAUDE_TOKEN_FILE, "claude code: token forgotten")}
    if account in forgotten:
        path, said = forgotten[account]
        path.unlink(missing_ok=True)
        print(said)
        return

    saved = common.load_json(common.ME_FILE)
    if not saved:
        print("me: not logged in")
        return
    if saved.get("session"):
        async def run():
            client = saved_client(saved)
            await client.connect()
            try:
                if await client.is_user_authorized():
                    await client.log_out()
            finally:
                await client.disconnect()

        asyncio.run(run())
    common.ME_FILE.unlink()
    print("me: session ended on Telegram's side and deleted here" if saved.get("session") else "me: saved api_id deleted")


def main(argv):
    match argv[1:]:
        case ["me"]:
            login_me()
        case ["bot"]:
            login_bot()
        case ["anthropic"]:
            login_anthropic()
        case ["claude"]:
            login_claude()
        case ["status"]:
            status()
        case ["logout", ("me" | "bot" | "anthropic" | "claude") as account]:
            logout(account)
        case []:
            print(__doc__)
        case _:
            print(__doc__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
