"""Slack: one parent message per pull request in the regressions channel, updates as thread replies.

Messages carry metadata {event_type: culprit, key}, which is how a rerun finds them again.
"""
import hashlib
import json
import os
import time

from slack_sdk import WebClient

from apps import http

EVENT = "culprit"


def client():
    http.load_env()
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def channel():
    return http.config()["slack"]["channel"]


def safe(text, limit=1500):
    """Attacker-controlled text (PR titles, logs) cannot ping people or break out of a code block."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("```", "'''")
    return text[:limit]


class Unanswered(Exception):
    """Slack kept returning an empty page for a conversation that cannot be empty."""


def _messages(fetch, attempts=6):
    """Slack sometimes answers with ok and no messages at all (seen live on Sep 13 2026, several times a minute).
    A channel CULPRIT posts in always holds at least the bot's join message, and a thread always holds its
    parent, so an empty page is no answer: ask again, and never read it as "not posted yet"."""
    for i in range(attempts):
        msgs = fetch()["messages"]
        if msgs:
            return msgs
        if i < attempts - 1:
            time.sleep(1.5 * (i + 1))
    raise Unanswered("Slack returned no messages for a conversation that has some; nothing was posted")


def _find(key, thread_ts=None):
    c = client()
    oldest = str(time.time() - 7 * 86400)
    if thread_ts:
        msgs = _messages(lambda: c.conversations_replies(channel=channel(), ts=thread_ts, include_all_metadata=True, limit=200))
    else:
        msgs = _messages(lambda: c.conversations_history(channel=channel(), oldest=oldest, include_all_metadata=True, limit=200))
    for m in msgs:
        meta = m.get("metadata") or {}
        if meta.get("event_type") == EVENT and (meta.get("event_payload") or {}).get("key") == key:
            return m
    return None


def version(text, blocks):
    return hashlib.sha256(json.dumps([text, blocks], sort_keys=True).encode()).hexdigest()[:12]


def upsert_parent(pr, text, blocks=None, fields=None):
    """Returns (ts, created). An existing parent is redrawn in place only when what it shows has changed."""
    key = str(pr)
    payload = {k: v for k, v in (fields or {}).items() if v is not None}
    payload.update(key=key, v=version(text, blocks))
    metadata = {"event_type": EVENT, "event_payload": payload}
    content = {"text": text, **({"blocks": blocks} if blocks else {})}
    found = _find(key)
    if found:
        shown = ((found.get("metadata") or {}).get("event_payload") or {}).get("v")
        if blocks and shown != payload["v"]:
            http.guard("slack", "chat.update", channel(), key)
            client().chat_update(channel=channel(), ts=found["ts"], metadata=metadata, **content)
            http.record("slack", "chat.update", channel(), key, found["ts"])
        return found["ts"], False
    http.guard("slack", "chat.postMessage", channel(), key)
    r = client().chat_postMessage(channel=channel(), unfurl_links=False, metadata=metadata, **content)
    http.record("slack", "chat.postMessage", channel(), key, r["ts"])
    return r["ts"], True


def reply_once(pr, parent_ts, subkey, text, blocks=None):
    key = f"{pr}:{subkey}"
    if _find(key, parent_ts):
        return False
    http.guard("slack", "chat.postMessage", channel(), key)
    r = client().chat_postMessage(channel=channel(), thread_ts=parent_ts, text=text, unfurl_links=False,
                                  metadata={"event_type": EVENT, "event_payload": {"key": key}},
                                  **({"blocks": blocks} if blocks else {}))
    http.record("slack", "chat.postMessage", channel(), key, r["ts"])
    return True


def permalink(ts):
    return client().chat_getPermalink(channel=channel(), message_ts=ts)["permalink"]


def thread(parent_ts):
    msgs = client().conversations_replies(channel=channel(), ts=parent_ts, limit=50)["messages"]
    return [{"text": m.get("text", ""), "ts": m["ts"]} for m in msgs[1:]]
