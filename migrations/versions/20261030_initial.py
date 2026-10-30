"""initial schema

Revision ID: 20261030_initial
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20261030_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create initial schema tables."""
    op.create_table(
        'events',
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('received_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_table(
        'tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_run_at', sa.Float(), nullable=False),
        sa.Column('locked_at', sa.Float(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id'),
    )
    op.create_index(
        'idx_tasks_claim',
        'tasks',
        ['status', 'next_run_at'],
    )


def downgrade() -> None:
    """Drop schema tables."""
    op.drop_index('idx_tasks_claim', table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('events')