"""add transaction_results table for scalability

Revision ID: 20250902142655
Revises: f9636f013003
Create Date: 2025-09-02 14:26:55

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250902142655'
down_revision = 'f9636f013003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create transaction_results table for ZeroMQ worker results
    op.create_table('transaction_results',
        sa.Column('tx_hash', sa.String(length=66), nullable=False),
        sa.Column('contract_address', sa.String(length=42), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('worker_id', sa.String(length=100), nullable=True),
        sa.Column('consensus_mode', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('tx_hash')
    )
    
    # Create indexes for efficient querying
    op.create_index('idx_transaction_results_contract', 'transaction_results', ['contract_address'])
    op.create_index('idx_transaction_results_processed_at', 'transaction_results', ['processed_at'])
    op.create_index('idx_transaction_results_contract_processed', 'transaction_results', ['contract_address', 'processed_at'])
    
    # Add comment to table
    op.execute("COMMENT ON TABLE transaction_results IS 'Stores results from distributed consensus workers via ZeroMQ'")


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_transaction_results_contract_processed', table_name='transaction_results')
    op.drop_index('idx_transaction_results_processed_at', table_name='transaction_results')
    op.drop_index('idx_transaction_results_contract', table_name='transaction_results')
    
    # Drop table
    op.drop_table('transaction_results')