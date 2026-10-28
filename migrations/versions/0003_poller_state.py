"""poller_state

Revision ID: 0003_poller_state
Revises: 0002_alerts_daily_stats
Create Date: 2026-10-28 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_poller_state'
down_revision = '0002_alerts_daily_stats'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('poller_state',
    sa.Column('service', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_ok_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_raw', sa.Text(), nullable=False),
    sa.Column('deltas', sa.Text(), nullable=False),
    sa.Column('traffic', sa.Text(), nullable=False),
    sa.Column('failures', sa.Integer(), nullable=False),
    sa.Column('rolled_through', sa.Date(), nullable=True),
    sa.PrimaryKeyConstraint('service')
    )


def downgrade() -> None:
    op.drop_table('poller_state')
