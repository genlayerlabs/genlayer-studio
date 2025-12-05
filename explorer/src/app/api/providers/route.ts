import { NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET() {
  try {
    const client = await pool.connect();

    try {
      const result = await client.query(`
        SELECT id, provider, model, config, plugin, plugin_config, is_default, created_at, updated_at
        FROM llm_provider
        ORDER BY provider, model
      `);

      return NextResponse.json({
        providers: result.rows,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
