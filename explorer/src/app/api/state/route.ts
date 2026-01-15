import { NextRequest, NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const search = searchParams.get('search');

    const client = await pool.connect();

    try {
      let query = 'SELECT * FROM current_state';
      const params: string[] = [];

      if (search) {
        query += ' WHERE id ILIKE $1';
        params.push(`%${search}%`);
      }

      query += ' ORDER BY updated_at DESC';

      const result = await client.query(query, params);

      return NextResponse.json({
        states: result.rows,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
