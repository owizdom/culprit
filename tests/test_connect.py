"""Tokens are written only after a live check passes, without disturbing other keys, and never readable by others."""
import os
import stat
import sys

import pytest

from app import connect
from apps import http


@pytest.fixture
def files(monkeypatch, tmp_path):
    env, public, local = tmp_path / ".env", tmp_path / "config.yaml", tmp_path / "config.local.yaml"
    env.write_text("ANTHROPIC_API_KEY=keep-me\nLINEAR_API_KEY=old\n")
    public.write_text("github:\n  repo: owizdom/picorv32-ci\nslack:\n  channel: ''\n")
    monkeypatch.setattr(http, "ENV", env)
    monkeypatch.setattr(http, "CONFIG", public)
    monkeypatch.setattr(http, "LOCAL_CONFIG", local)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    return {"env": env, "local": local}


def test_a_token_that_fails_its_check_is_not_written(monkeypatch, files):
    monkeypatch.setitem(connect.CHECKS, "linear", lambda v: connect.check(error="Linear rejected this key.", token=v))
    result = connect.save_token("linear", "lin_api_wrong")
    assert result["connected"] is False and result["error"] == "Linear rejected this key."
    assert "lin_api_wrong" not in files["env"].read_text()


def test_a_passing_token_replaces_only_its_own_key(monkeypatch, files):
    monkeypatch.setitem(connect.CHECKS, "linear", lambda v: connect.check(True, "wisdom", token=v))
    result = connect.save_token("linear", "  lin_api_good1234 ")
    text = files["env"].read_text()
    assert "LINEAR_API_KEY=lin_api_good1234" in text and "LINEAR_API_KEY=old" not in text
    assert "ANTHROPIC_API_KEY=keep-me" in text
    if sys.platform != "win32":          # Windows has no POSIX permission bits
        assert stat.S_IMODE(os.stat(files["env"]).st_mode) == 0o600
    assert os.environ["LINEAR_API_KEY"] == "lin_api_good1234"
    assert result["token_hint"] == "ending 1234" and "lin_api_good1234" not in str(result)


def test_disconnect_removes_the_key(monkeypatch, files):
    monkeypatch.setitem(connect.CHECKS, "linear", lambda v: connect.check(error="Not connected."))
    connect.disconnect("linear")
    assert "LINEAR_API_KEY" not in files["env"].read_text() and "ANTHROPIC_API_KEY=keep-me" in files["env"].read_text()


def test_the_claude_key_is_saved_only_after_its_check_passes(monkeypatch, files):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(connect, "check_anthropic", lambda v: connect.check(error="Anthropic rejected this key.", token=v))
    assert not connect.save_anthropic("sk-ant-wrong")["connected"]
    assert "sk-ant-wrong" not in files["env"].read_text()
    monkeypatch.setattr(connect, "check_anthropic", lambda v: connect.check(True, "Claude API key", token=v))
    assert connect.save_anthropic("sk-ant-good1")["connected"]
    assert "ANTHROPIC_API_KEY=sk-ant-good1" in files["env"].read_text() and "keep-me" not in files["env"].read_text()


def test_logout_removes_every_app_token_and_keeps_the_rest(monkeypatch, files):
    files["env"].write_text("ANTHROPIC_API_KEY=keep-me\nGITHUB_TOKEN=g\nLINEAR_API_KEY=l\nSLACK_BOT_TOKEN=s\n")
    for app, key in http.TOKENS.items():
        monkeypatch.setenv(key, "set")
        monkeypatch.setitem(connect.CHECKS, app, lambda v: connect.check(error="Not connected.") if not v else connect.check(True))
    monkeypatch.setattr(connect, "check_anthropic", lambda v: connect.check(True))
    result = connect.logout()
    text = files["env"].read_text()
    assert text == "ANTHROPIC_API_KEY=keep-me\n"
    assert not any(os.environ.get(key) for key in http.TOKENS.values())
    assert not any(result[app]["connected"] for app in http.TOKENS)


def test_setup_goes_to_the_local_file_and_overrides_the_public_one(monkeypatch, files):
    monkeypatch.setattr(connect, "linear_teams", lambda: {"viewer_id": "user-1", "teams": [
        {"id": "team-1", "key": "WIS", "name": "Wisdom", "done_state_id": "done-1"}]})
    monkeypatch.setitem(connect.CHECKS, "github", lambda v: connect.check(True, "owizdom"))
    monkeypatch.setattr(connect, "check_github", lambda v: connect.check(True, "owizdom"))
    s = connect.save_setup(repo="owizdom/other", team_id="team-1", channel="C0C1KQR847N")
    cfg = http.config()
    assert cfg["github"]["repo"] == "owizdom/other" and cfg["slack"]["channel"] == "C0C1KQR847N"
    assert cfg["linear"]["done_state_id"] == "done-1" and cfg["linear"]["users"] == {"owizdom": "user-1"}
    assert s["team"]["key"] == "WIS"


def test_repositories_come_back_newest_first_with_only_what_the_picker_shows(monkeypatch, files):
    monkeypatch.setenv("GITHUB_TOKEN", "gho_test")
    asked = {}

    class Reply:
        status_code = 200

        def json(self):
            return [{"full_name": "owizdom/picorv32-ci", "private": False, "pushed_at": "2026-09-13T16:50:02Z",
                     "description": "PicoRV32 with CI", "owner": {"login": "owizdom"}, "size": 4096}]

    def fake_get(url, **kw):
        asked.update(url=url, params=kw["params"])
        return Reply()

    monkeypatch.setattr(connect.httpx, "get", fake_get)
    assert connect.list_repos() == [{"full_name": "owizdom/picorv32-ci", "private": False,
                                     "pushed_at": "2026-09-13T16:50:02Z", "description": "PicoRV32 with CI"}]
    assert asked["url"].endswith("/user/repos") and asked["params"]["sort"] == "pushed"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert connect.list_repos() == {"error": "Connect GitHub first."}


def test_channel_id_comes_from_a_link_or_an_id():
    assert connect.channel_id("https://culprit.slack.com/archives/C0C1KQR847N") == "C0C1KQR847N"
    assert connect.channel_id(" C0C1KQR847N ") == "C0C1KQR847N"
    assert connect.channel_id("#general") is None


def test_a_token_with_spaces_is_refused(files):
    with pytest.raises(ValueError):
        connect.write_env("LINEAR_API_KEY", "two words")
