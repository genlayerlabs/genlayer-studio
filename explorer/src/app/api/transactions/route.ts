import { NextRequest, NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const page = parseInt(searchParams.get('page') || '1');
    const limit = parseInt(searchParams.get('limit') || '20');
    const status = searchParams.get('status');
    const search = searchParams.get('search');
    const offset = (page - 1) * limit;

    const client = await pool.connect();

    try {
      let whereClause = '';
      const params: (string | number)[] = [];
      let paramIndex = 1;

      if (status) {
        whereClause += `WHERE status = $${paramIndex}`;
        params.push(status);
        paramIndex++;
      }

      if (search) {
        const searchCondition = `(hash ILIKE $${paramIndex} OR from_address ILIKE $${paramIndex} OR to_address ILIKE $${paramIndex})`;
        if (whereClause) {
          whereClause += ` AND ${searchCondition}`;
        } else {
          whereClause = `WHERE ${searchCondition}`;
        }
        params.push(`%${search}%`);
        paramIndex++;
      }

      // Get total count
      const countQuery = `SELECT COUNT(*) as count FROM transactions ${whereClause}`;
      const countResult = await client.query(countQuery, params);
      const total = parseInt(countResult.rows[0].count);

      // Get transactions with triggered count
      const query = `
        SELECT t.*,
          (SELECT COUNT(*) FROM transactions WHERE triggered_by_hash = t.hash) as triggered_count
        FROM transactions t
        ${whereClause}
        ORDER BY t.created_at DESC
        LIMIT $${paramIndex} OFFSET $${paramIndex + 1}
      `;
      params.push(limit, offset);

      const result = await client.query(query, params);

      return NextResponse.json({
        transactions: result.rows,
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit),
        },
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
