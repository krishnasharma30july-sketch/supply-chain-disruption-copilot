"""outreach items: call briefs and seller notices

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('outreach_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('ref', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('generator', sa.String(length=10), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('prompt_version', sa.String(length=32), nullable=True),
    sa.Column('qa', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('priority', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outreach_items_run_id'), 'outreach_items', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_outreach_items_run_id'), table_name='outreach_items')
    op.drop_table('outreach_items')
