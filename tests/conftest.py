import json
from types import SimpleNamespace

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    import app.db.session as session_mod
    monkeypatch.setattr(session_mod, "SessionLocal", Session)
    with Session() as s:
        yield s


@pytest.fixture(autouse=True)
def no_backup_provider(monkeypatch):
    """Tests never reach a real backup provider, whatever the developer's environment holds."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "backup_llm_api_key", None)
    monkeypatch.setattr(get_settings(), "orchestrator", "loop")


@pytest.fixture(autouse=True)
def quiet_alerts(monkeypatch):
    """Record alerts instead of sending them."""
    sent = []
    from app.services import notify
    monkeypatch.setattr(notify, "alert", lambda level, title, body, link=None: sent.append((level, title)) or {})
    return sent


def tool_call(name, args, i=0):
    return SimpleNamespace(id=f"call_{name}_{i}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args) if isinstance(args, dict) else args))


class ScriptedClient:
    """Stands in for the Groq client: returns the scripted replies in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(dict(kwargs, messages=list(kwargs.get("messages", []))))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content, calls = reply if isinstance(reply, tuple) else (None, reply)
        msg = SimpleNamespace(content=content, tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


@pytest.fixture()
def scripted():
    return ScriptedClient


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    from app.core.security import HITS
    HITS.clear()
    yield
