"""Connect GitHub, Linear and Slack from the app.

Every check is a live, read-only call to the service. A token is saved only after its check passes,
into .env (mode 600); the choices made during setup go to config.local.yaml. Neither file is committed.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import yaml

from apps import http
from runs import store

GITHUB, LINEAR, SLACK = "https://api.github.com", "https://api.linear.app/graphql", "https://slack.com/api"
CACHE_SECONDS = 60
_lock = threading.Lock()
_cache = {"at": 0.0, "value": None}
_device = {}


def check(connected=False, identity=None, detail=None, scopes=(), error=None, fix=None, token=None):
    return {"connected": connected, "identity": identity, "detail": detail, "scopes": list(scopes),
            "error": error, "fix": fix, "token_hint": f"ending {token[-4:]}" if token else None}


def token(app):
    http.load_env()
    return os.environ.get(http.TOKENS[app]) or None


# Live checks

def check_github(value):
    if not value:
        return check(error="Not connected.", fix="Sign in with GitHub, use your GitHub CLI login, or paste a token.")
    try:
        r = httpx.get(f"{GITHUB}/user", timeout=15, headers={"Authorization": f"Bearer {value}",
                                                            "Accept": "application/vnd.github+json"})
    except httpx.HTTPError as e:
        return check(error=f"Could not reach GitHub: {e}", fix="Check your internet connection.", token=value)
    if r.status_code == 401:
        return check(error="GitHub rejected this token.", fix="Sign in again or create a new token.", token=value)
    if r.status_code != 200:
        return check(error=f"GitHub answered {r.status_code}.", token=value)
    user = r.json()
    scopes = [s.strip() for s in (r.headers.get("X-OAuth-Scopes") or "").split(",") if s.strip()]
    return check(True, user["login"], user.get("name"), scopes, token=value)


def check_linear(value):
    if not value:
        return check(error="Not connected.", fix="Paste a Linear API key (Settings > Security & access > API keys).")
    try:
        r = httpx.post(LINEAR, timeout=15, headers={"Authorization": value, "Content-Type": "application/json"},
                       json={"query": "{ viewer { name } organization { name urlKey } }"})
    except httpx.HTTPError as e:
        return check(error=f"Could not reach Linear: {e}", fix="Check your internet connection.", token=value)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or body.get("errors") or not body.get("data"):
        return check(error="Linear rejected this key.", fix="Create a new personal API key in Linear and paste it.",
                     token=value)
    data = body["data"]
    return check(True, data["viewer"]["name"], f"workspace {data['organization']['name']}", token=value)


SLACK_ERRORS = {
    "invalid_auth": ("Slack rejected this token.", "Copy the Bot User OAuth Token again from OAuth & Permissions."),
    "not_allowed_token_type": ("That is not a bot token.",
                               "Paste the Bot User OAuth Token (it starts with xoxb-) from OAuth & Permissions."),
    "token_revoked": ("This token was revoked.", "Reinstall the app to your workspace and copy the new token."),
    "not_in_channel": ("The bot is not in that channel.", "In the channel, type /invite @culprit and try again."),
    "channel_not_found": ("Slack cannot find that channel.", "Copy the channel link again (channel name > Copy link)."),
    "missing_scope": ("The Slack app is missing a permission.", "Reinstall the app from apps/slack_manifest.yaml."),
}


def slack_call(value, method, **params):
    r = httpx.post(f"{SLACK}/{method}", timeout=15, data=params, headers={"Authorization": f"Bearer {value}"})
    return r.json()


def check_slack(value):
    if not value:
        return check(error="Not connected.", fix="Install the CULPRIT Slack app and paste its bot token.")
    try:
        body = slack_call(value, "auth.test")
    except httpx.HTTPError as e:
        return check(error=f"Could not reach Slack: {e}", fix="Check your internet connection.", token=value)
    if not body.get("ok"):
        error, fix = SLACK_ERRORS.get(body.get("error"), (f"Slack said {body.get('error')}.", None))
        return check(error=error, fix=fix, token=value)
    name = slack_app_name(value, body.get("bot_id"))
    return check(True, name or f"@{body['user']}", f"workspace {body['team']}", token=value)


def slack_app_name(value, bot_id):
    """The name people see on the bot's messages. auth.test returns only the bot's @handle, which Slack keeps when the
    app is renamed, and bots.info needs a scope the app does not ask for. The bot's own messages in the configured
    channel carry the app's current name; None until it has posted there."""
    channel = (http.config().get("slack") or {}).get("channel")
    if not channel or not bot_id:
        return None
    try:
        body = slack_call(value, "conversations.history", channel=channel, limit=100)
    except httpx.HTTPError:
        return None
    return next((m["bot_profile"]["name"] for m in body.get("messages") or []
                 if m.get("bot_id") == bot_id and (m.get("bot_profile") or {}).get("name")), None)


CHECKS = {"github": check_github, "linear": check_linear, "slack": check_slack}
ANTHROPIC = "ANTHROPIC_API_KEY"


def check_anthropic(value):
    """The model key is the agent's, not an app account, so it is checked and saved apart from the three apps."""
    if not value:
        return check(error="No Claude API key.", fix="Paste an Anthropic API key. Without it, fixes that need the model are skipped.")
    try:
        r = httpx.get("https://api.anthropic.com/v1/models", timeout=15,
                      headers={"x-api-key": value, "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as e:
        return check(error=f"Could not reach Anthropic: {e}", fix="Check your internet connection.", token=value)
    if r.status_code == 401:
        return check(error="Anthropic rejected this key.", fix="Create a new key in the Anthropic Console and paste it.",
                     token=value)
    if r.status_code != 200:
        return check(error=f"Anthropic answered {r.status_code}.", token=value)
    return check(True, "works", "checked with Anthropic", token=value)


def save_anthropic(value):
    value = (value or "").strip()
    result = check_anthropic(value) if value and not re.search(r"\s", value) else check(error="That does not look like a key.")
    if result["connected"]:
        write_env(ANTHROPIC, value)
    return result


def watcher():
    path = store.RUNS / "watcher.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"state": "starting", "error": None}


def setup():
    cfg = http.config()
    linear = cfg.get("linear") or {}
    return {"repo": (cfg.get("github") or {}).get("repo") or None,
            "team": {"id": linear["team_id"], "key": linear.get("team_key"), "name": linear.get("team_name")}
            if linear.get("team_id") else None,
            "channel": {"id": (cfg.get("slack") or {})["channel"], "name": (cfg.get("slack") or {}).get("channel_name")}
            if (cfg.get("slack") or {}).get("channel") else None}


def connections(force=False):
    """Live status of all three apps, checked in parallel and cached for a minute."""
    if not force and _cache["value"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return dict(_cache["value"], setup=setup(), watcher=watcher())
    with ThreadPoolExecutor(4) as pool:
        apps = {app: pool.submit(CHECKS[app], token(app)) for app in CHECKS}
        model = pool.submit(check_anthropic, os.environ.get(ANTHROPIC) or None)
        results = {app: f.result() for app, f in apps.items()}
        results["anthropic"] = model.result()
    if results["linear"]["connected"]:
        name_the_team()
    _cache.update(at=time.time(), value=results)
    return dict(results, setup=setup(), watcher=watcher())


def name_the_team():
    """A config that only has the team id (written by hand) gets its key and name once, so the window can show them."""
    linear = http.config().get("linear") or {}
    if not linear.get("team_id") or linear.get("team_name"):
        return
    try:
        team = next((t for t in linear_teams()["teams"] if t["id"] == linear["team_id"]), None)
    except (httpx.HTTPError, ValueError):
        return
    if team:
        write_local_config({"linear": {"team_key": team["key"], "team_name": team["name"]}})


# Saving

def write_env(key, value):
    """Set or remove one key in .env without touching the others. Written to a temp file, then renamed."""
    if value is not None and (not value or re.search(r"\s", value)):
        raise ValueError("a token cannot be empty or contain spaces")
    with _lock:
        lines = http.ENV.read_text().splitlines() if http.ENV.exists() else []
        kept = [line for line in lines if not line.startswith(f"{key}=")]
        if value is not None:
            kept.append(f"{key}={value}")
        _replace(http.ENV, "\n".join(kept) + "\n")
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    _cache["value"] = None


def write_local_config(patch):
    with _lock:
        _replace(http.LOCAL_CONFIG, yaml.safe_dump(http.merged(http.read_yaml(http.LOCAL_CONFIG), patch), sort_keys=False))
    _cache["value"] = None


def _replace(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def save_token(app, value):
    value = (value or "").strip()
    result = CHECKS[app](value) if value and not re.search(r"\s", value) else check(error="That does not look like a token.")
    if result["connected"]:
        write_env(http.TOKENS[app], value)
    return result


def disconnect(app):
    write_env(http.TOKENS[app], None)
    return CHECKS[app](None)


def logout():
    """Remove every app token from .env and this process. The agent's own keys and the setup choices stay."""
    for key in http.TOKENS.values():
        write_env(key, None)
    return connections(force=True)


def use_cli():
    try:
        value = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        value = ""
    if not value:
        return check(error="The GitHub CLI is not signed in on this computer.", fix="Run gh auth login, or use another option.")
    return save_token("github", value)


# GitHub device flow: the app shows a code, the user approves it on github.com, the app polls for the token.

def device_start():
    client_id = (http.config().get("github") or {}).get("client_id")
    if not client_id:
        return {"error": "Sign in with GitHub is not set up yet: github.client_id is missing from config.yaml."}
    r = httpx.post("https://github.com/login/device/code", timeout=15, headers={"Accept": "application/json"},
                   data={"client_id": client_id, "scope": "public_repo"})
    body = r.json()
    if "device_code" not in body:
        return {"error": body.get("error_description") or body.get("error") or f"GitHub answered {r.status_code}."}
    _device.update(client_id=client_id, device_code=body["device_code"], interval=body.get("interval", 5),
                   expires=time.time() + body.get("expires_in", 900))
    return {"user_code": body["user_code"], "verification_uri": body["verification_uri"], "interval": _device["interval"]}


def device_poll():
    if not _device:
        return {"state": "error", "error": "Start sign in first."}
    if time.time() > _device["expires"]:
        _device.clear()
        return {"state": "expired"}
    r = httpx.post("https://github.com/login/oauth/access_token", timeout=15, headers={"Accept": "application/json"},
                   data={"client_id": _device["client_id"], "device_code": _device["device_code"],
                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    body = r.json()
    if body.get("access_token"):
        _device.clear()
        return {"state": "done", "check": save_token("github", body["access_token"])}
    error = body.get("error")
    if error == "slow_down":
        _device["interval"] = body.get("interval", _device["interval"] + 5)
    if error in ("authorization_pending", "slow_down"):
        return {"state": "pending", "interval": _device["interval"]}
    _device.clear()
    return {"state": {"expired_token": "expired", "access_denied": "denied"}.get(error, "error"),
            "error": body.get("error_description")}


# Choosing what to watch and where to post

def list_repos():
    """The signed-in user's repositories, most recently pushed first, for the repository picker."""
    value = token("github")
    if not value:
        return {"error": "Connect GitHub first."}
    try:
        r = httpx.get(f"{GITHUB}/user/repos", timeout=20,
                      headers={"Authorization": f"Bearer {value}", "Accept": "application/vnd.github+json"},
                      params={"per_page": 100, "sort": "pushed", "affiliation": "owner,collaborator,organization_member"})
    except httpx.HTTPError as e:
        return {"error": f"Could not reach GitHub: {e}"}
    if r.status_code != 200:
        return {"error": f"GitHub answered {r.status_code}."}
    return [{"full_name": x["full_name"], "private": x["private"], "pushed_at": x.get("pushed_at"),
             "description": x.get("description")} for x in r.json()]


def check_repo(full_name):
    full_name = (full_name or "").strip().removeprefix("https://github.com/").strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", full_name):
        return {"ok": False, "error": "Use the owner/name form, for example owizdom/picorv32-ci."}
    headers = {"Authorization": f"Bearer {token('github')}", "Accept": "application/vnd.github+json"}
    r = httpx.get(f"{GITHUB}/repos/{full_name}", timeout=15, headers=headers)
    if r.status_code == 404:
        return {"ok": False, "error": "GitHub cannot find that repository with this login."}
    if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
        reset = time.strftime("%H:%M", time.localtime(int(r.headers.get("x-ratelimit-reset") or time.time())))
        return {"ok": False, "error": f"GitHub's hourly API limit for this account is used up. It resets at {reset}; "
                                      "try again then."}
    if r.status_code != 200:
        return {"ok": False, "error": f"GitHub answered {r.status_code}."}
    repo = r.json()
    flows = httpx.get(f"{GITHUB}/repos/{full_name}/actions/workflows", timeout=15, headers=headers)
    names = [w["name"] for w in flows.json().get("workflows", [])] if flows.status_code == 200 else []
    return {"ok": True, "name": repo["full_name"], "private": repo["private"],
            "can_push": bool((repo.get("permissions") or {}).get("push")), "workflows": names,
            "error": None if names else "No GitHub Actions workflow found; CULPRIT needs CI runs to watch."}


def linear_teams():
    r = httpx.post(LINEAR, timeout=15, headers={"Authorization": token("linear") or "", "Content-Type": "application/json"},
                   json={"query": "{ viewer { id } teams { nodes { id key name states { nodes { id type } } } } }"})
    data = r.json().get("data") or {}
    return {"viewer_id": (data.get("viewer") or {}).get("id"),
            "teams": [{"id": t["id"], "key": t["key"], "name": t["name"],
                       "done_state_id": next((s["id"] for s in t["states"]["nodes"] if s["type"] == "completed"), None)}
                      for t in (data.get("teams") or {}).get("nodes", [])]}


def channel_id(text):
    m = re.search(r"\b([CG][A-Z0-9]{8,})\b", (text or "").strip())
    return m.group(1) if m else None


def slack_channel(text):
    cid = channel_id(text)
    if not cid:
        return {"ok": False, "id": None, "error": "Paste the channel link (channel name > Copy link) or its ID."}
    body = slack_call(token("slack") or "", "conversations.history", channel=cid, limit=1)
    if not body.get("ok"):
        error, fix = SLACK_ERRORS.get(body.get("error"), (f"Slack said {body.get('error')}.", None))
        return {"ok": False, "id": cid, "error": error, "fix": fix}
    return {"ok": True, "id": cid, "error": None}


def save_setup(repo=None, team_id=None, channel=None, channel_name=None):
    patch = {}
    if repo:
        patch["github"] = {"repo": repo}
    if team_id:
        found = linear_teams()
        team = next((t for t in found["teams"] if t["id"] == team_id), None)
        if team is None:
            raise ValueError("that Linear team is not visible with this key")
        patch["linear"] = {"team_id": team["id"], "team_key": team["key"], "team_name": team["name"],
                           "done_state_id": team["done_state_id"]}
        login = check_github(token("github")).get("identity")
        if login and found["viewer_id"]:
            patch["linear"]["users"] = {login: found["viewer_id"]}
    if channel:
        patch["slack"] = {"channel": channel, **({"channel_name": channel_name} if channel_name else {})}
    if patch:
        write_local_config(patch)
    return setup()
