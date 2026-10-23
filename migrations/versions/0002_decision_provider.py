"""decisions.provider: which AI provider (or the rules) made the decision

Revision ID: 0002_decision_provider
Revises: 0001_initial
Create Date: 2026-10-23 17:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_decision_provider'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.add_column(sa.Column('provider', sa.String(length=12), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.drop_column('provider')
