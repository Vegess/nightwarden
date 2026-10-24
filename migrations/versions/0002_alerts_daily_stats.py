"""alerts and daily_stats

Revision ID: 0002_alerts_daily_stats
Revises: 0001_initial
Create Date: 2026-10-24 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_alerts_daily_stats'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('alerts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('error_short', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alerts_ts'), ['ts'], unique=False)

    op.create_table('daily_stats',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('polls', sa.Integer(), nullable=False),
    sa.Column('polls_ok', sa.Integer(), nullable=False),
    sa.Column('events', sa.Integer(), nullable=False),
    sa.Column('decisions_by_action', sa.Text(), nullable=False),
    sa.Column('decisions_by_decided_by', sa.Text(), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('decision_ms_p50', sa.Integer(), nullable=True),
    sa.Column('decision_ms_p95', sa.Integer(), nullable=True),
    sa.Column('incidents_opened', sa.Integer(), nullable=False),
    sa.Column('incidents_resolved', sa.Integer(), nullable=False),
    sa.Column('pages', sa.Integer(), nullable=False),
    sa.Column('emails_sent', sa.Integer(), nullable=False),
    sa.Column('emails_failed', sa.Integer(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('date')
    )


def downgrade() -> None:
    op.drop_table('daily_stats')
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alerts_ts'))
    op.drop_table('alerts')
