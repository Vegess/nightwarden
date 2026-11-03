"""neon transfer month-to-date baseline on poller_state

Revision ID: 0006_neon_month
Revises: 0005_perf_reports
Create Date: 2026-11-03 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_neon_month'
down_revision = '0005_perf_reports'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('poller_state') as batch_op:
        batch_op.add_column(sa.Column('neon_month', sa.String(length=7), nullable=True))
        batch_op.add_column(sa.Column('neon_transfer_base', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('neon_transfer_last', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('poller_state') as batch_op:
        for col in ('neon_transfer_last', 'neon_transfer_base', 'neon_month'):
            batch_op.drop_column(col)
