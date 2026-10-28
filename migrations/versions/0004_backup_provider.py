"""decisions.provider and the backup AI check on poller_state

Revision ID: 0004_backup_provider
Revises: 0003_poller_state
Create Date: 2026-10-28 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_backup_provider'
down_revision = '0003_poller_state'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.add_column(sa.Column('provider', sa.String(length=12), nullable=True))
    with op.batch_alter_table('poller_state') as batch_op:
        batch_op.add_column(sa.Column('backup_checked_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('backup_ok_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('backup_error', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('poller_state') as batch_op:
        batch_op.drop_column('backup_error')
        batch_op.drop_column('backup_ok_at')
        batch_op.drop_column('backup_checked_at')
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.drop_column('provider')
