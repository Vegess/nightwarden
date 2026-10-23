"""DATABASE_URL credentials never reach the logs or error messages."""
import logging
import os
import subprocess
import sys

from app.core.logging import configure_logging, redact_url

URL = "postgresql://nightwarden:s3cretPw@db.example.com:5432/nw?sslmode=require"


def test_redact_url_masks_only_the_password():
    assert redact_url(URL) == "postgresql://nightwarden:***@db.example.com:5432/nw?sslmode=require"
    assert redact_url("sqlite:///./nightwarden.db") == "sqlite:///./nightwarden.db"
    assert "s3cretPw" not in redact_url(f"failed to connect to {URL} (timeout)")


def test_url_with_user_and_password_never_appears_in_captured_logs(capsys):
    for json_logs in (True, False):
        configure_logging(json_logs)
        log = logging.getLogger("nightwarden.test")
        log.info("Database: %s", URL)
        log.warning(f"connect failed for {URL}")
        try:
            raise ConnectionError(f"could not reach {URL}")
        except ConnectionError:
            log.exception("Poll cycle failed")
    err = capsys.readouterr().err
    assert "Database:" in err and "Poll cycle failed" in err and "could not reach" in err
    assert "s3cretPw" not in err and "nightwarden:s3cretPw@" not in err
    assert "nightwarden:***@db.example.com" in err


def test_migration_errors_do_not_print_the_password(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "DATABASE_URL": "postgresql://nightwarden:s3cretPw@127.0.0.1:1/nw"}
    out = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "Migration failed on postgresql://nightwarden:***@127.0.0.1:1/nw" in out.stderr
    assert "s3cretPw" not in out.stdout + out.stderr
