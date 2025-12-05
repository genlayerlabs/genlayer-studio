import { NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET() {
  try {
    const client = await pool.connect();

    try {
      const result = await client.query(`
        SELECT id, stake, config, address, provider, model, plugin, plugin_config, created_at
        FROM validators
        ORDER BY id
      `);

      return NextResponse.json({
        validators: result.rows,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
