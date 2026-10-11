from fastapi.testclient import TestClient


def client(db, monkeypatch, admin="secret"):
    import app.db.session as session_mod
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "poller_enabled", False)
    monkeypatch.setattr(s, "admin_token", admin)
    from app.main import app
    from app.db.session import get_db

    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_public_reads_and_admin_writes(db, monkeypatch):
    c = client(db, monkeypatch)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/").status_code == 200
    st = c.get("/api/status").json()
    assert st["service"] == "citebase" and st["reachable"] is False
    assert c.post("/api/deploys", json={"commit": "abc"}).status_code == 401
    assert c.post("/api/deploys", json={"commit": "abc"}, headers={"Authorization": "Bearer secret"}).status_code == 201
    d = c.post("/api/events", json={"kind": "error_spike", "detail": "test", "metrics": {"error_rate": 0.5}},
               headers={"Authorization": "Bearer secret"}).json()
    assert d["degraded"] is True and d["action"] == "page"  # rules: error_rate 0.5 -> page
    incs = c.get("/api/incidents").json()
    assert len(incs) == 1
    detail = c.get(f"/api/incidents/{incs[0]['id']}").json()
    assert detail["decisions"][0]["event"]["kind"] == "error_spike"
    assert c.post(f"/api/incidents/{incs[0]['id']}/resolve", headers={"Authorization": "Bearer secret"}).json()["status"] == "resolved"
    assert c.get("/api/eval").status_code == 200


def test_eval_endpoint_returns_a_summary(db, monkeypatch):
    r = client(db, monkeypatch).get("/api/eval").json()
    if r["available"]:
        s = r["summary"]
        assert s["baseline"]["n"] == len(s["per_scenario"]) * 5
        assert {"accuracy", "silent_recall", "page_recall"} <= s["baseline"].keys()


def test_dashboard_reads_the_trace_fields_the_orchestrator_writes():
    """The dashboard renders tool_calls[].tool/args/result_preview; keep both sides in step."""
    import inspect
    from pathlib import Path
    from app.agent import orchestrator
    src = inspect.getsource(orchestrator)
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text()
    for field in ("tool", "args", "result_preview"):
        assert f'"{field}"' in src and f"c.{field}" in html
