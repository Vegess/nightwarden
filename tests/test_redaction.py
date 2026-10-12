"""What reaches the public dashboard and the model from the watched service's logs."""
from app.collector import target

SQLA = """Traceback (most recent call last):
  File "x.py", line 1, in f
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column documents.collection_id does not exist
[SQL: SELECT * FROM chat_messages WHERE content = %(q)s]
[parameters: {'q': 'what is the salary of priya in the leaked HR file?'}]
(Background on this error at: https://sqlalche.me/e/20/f405)"""

GROQ = ("groq.BadRequestError: Error code: 400 - {'error': {'message': 'Failed to generate JSON', "
        "'code': 'json_validate_failed', 'failed_generation': 'According to the Q3 board memo, revenue fell 40%'}}")


def test_sqlalchemy_parameters_never_survive_and_the_useful_line_does():
    line = target.exception_line(SQLA)
    assert "UndefinedColumn" in line and "collection_id" in line
    assert "priya" not in line and "salary" not in line and "Background" not in line


def test_provider_errors_lose_the_quoted_generation():
    out = target.redact(GROQ)
    assert "json_validate_failed" in out and "board memo" not in out and "payload removed" in out


def test_credentials_ids_and_emails_are_masked():
    out = target.redact("auth failed for anirudh@example.com doc 3f2b1c9e-1d2a-4b3c-8d4e-5f6a7b8c9d0e "
                        "key gsk_abcdefghijklmnopqrstuvwx Bearer abcdefghijklmnop1234 hash " + "a" * 40)
    for leaked in ("anirudh@", "3f2b1c9e", "gsk_abc", "abcdefghijklmnop1234", "a" * 40):
        assert leaked not in out
    assert out.count("<secret>") >= 3 and "<email>" in out and "<id>" in out


def test_fetch_logs_sanitizes_every_field(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self):
            return [{"ts": "t", "level": "ERROR", "logger": "app", "msg": "Ingest failed for bob@x.io",
                     "exc": SQLA, "endpoint": "/documents/3f2b1c9e-1d2a-4b3c-8d4e-5f6a7b8c9d0e", "secret_field": "x"}]
    monkeypatch.setattr(target.httpx, "get", lambda *a, **k: R())
    [row] = target.fetch_logs()
    assert "secret_field" not in row and "<email>" in row["msg"] and "<id>" in row["endpoint"]
    assert "priya" not in str(row)
