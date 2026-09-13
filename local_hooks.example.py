"""Additions of your own to responder.py, kept out of the repository: copy this file to local_hooks.py and fill it in.

SYSTEM_EXTRA is added at the end of what Claude is told, with {bot_name} and nothing else in braces - how the bot
speaks of itself, say. reply_note(chat_id, message_id) is asked about the message a question replies to and returns a
sentence for Claude about it, or None - for messages another script of yours posted through the bot."""

SYSTEM_EXTRA = ""


def reply_note(chat_id, message_id):
    return None
