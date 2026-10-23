import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.logging import RedactFilter, redact_url
from app.db.base import Base
import app.db.models  # noqa: F401 — register tables

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactFilter())
_url = get_settings().database_url
# ConfigParser treats % as interpolation; escape it so URL-encoded passwords survive.
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


try:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
except Exception as exc:  # noqa: BLE001 — driver errors can quote the URL; never print the password
    raise RuntimeError(f"Migration failed on {redact_url(_url)}: {redact_url(exc)}") from None
