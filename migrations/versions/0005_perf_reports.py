"""shadow baseline, report columns on daily_stats, weekly digest marker

Revision ID: 0005_perf_reports
Revises: 0004_backup_provider
Create Date: 2026-10-28 11:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_perf_reports'
down_revision = '0004_backup_provider'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.add_column(sa.Column('rules_action', sa.String(length=10), nullable=True))
    with op.batch_alter_table('daily_stats') as batch_op:
        batch_op.add_column(sa.Column('incidents_by_severity', sa.Text(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('events_by_kind', sa.Text(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('resolve_s_sum', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('decisions_degraded', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    with op.batch_alter_table('poller_state') as batch_op:
        batch_op.add_column(sa.Column('digest_week', sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('poller_state') as batch_op:
        batch_op.drop_column('digest_week')
    with op.batch_alter_table('daily_stats') as batch_op:
        for col in ('version', 'decisions_degraded', 'resolve_s_sum', 'events_by_kind', 'incidents_by_severity'):
            batch_op.drop_column(col)
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.drop_column('rules_action')
