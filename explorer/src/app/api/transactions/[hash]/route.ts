import { NextRequest, NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ hash: string }> }
) {
  try {
    const { hash } = await params;
    const client = await pool.connect();

    try {
      // Get the transaction
      const txResult = await client.query(
        'SELECT * FROM transactions WHERE hash = $1',
        [hash]
      );

      if (txResult.rows.length === 0) {
        return NextResponse.json({ error: 'Transaction not found' }, { status: 404 });
      }

      const transaction = txResult.rows[0];

      // Get triggered transactions
      const triggeredResult = await client.query(
        'SELECT * FROM transactions WHERE triggered_by_hash = $1 ORDER BY created_at',
        [hash]
      );

      // Get parent transaction if this was triggered
      let parentTransaction = null;
      if (transaction.triggered_by_hash) {
        const parentResult = await client.query(
          'SELECT * FROM transactions WHERE hash = $1',
          [transaction.triggered_by_hash]
        );
        if (parentResult.rows.length > 0) {
          parentTransaction = parentResult.rows[0];
        }
      }

      return NextResponse.json({
        transaction,
        triggeredTransactions: triggeredResult.rows,
        parentTransaction,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
