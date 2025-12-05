import { NextRequest, NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const client = await pool.connect();

    try {
      // Get the state
      const stateResult = await client.query(
        'SELECT * FROM current_state WHERE id = $1',
        [id]
      );

      if (stateResult.rows.length === 0) {
        return NextResponse.json({ error: 'State not found' }, { status: 404 });
      }

      // Get related transactions (to this address)
      const txResult = await client.query(
        `SELECT * FROM transactions
         WHERE to_address = $1 OR from_address = $1
         ORDER BY created_at DESC
         LIMIT 50`,
        [id]
      );

      return NextResponse.json({
        state: stateResult.rows[0],
        transactions: txResult.rows,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
