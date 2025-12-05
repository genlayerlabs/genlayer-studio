import { NextResponse } from 'next/server';
import pool from '@/lib/db';

export async function GET() {
  try {
    const client = await pool.connect();

    try {
      // Get total transactions
      const totalTxResult = await client.query('SELECT COUNT(*) as count FROM transactions');
      const totalTransactions = parseInt(totalTxResult.rows[0].count);

      // Get transactions by status
      const statusResult = await client.query(`
        SELECT status, COUNT(*) as count
        FROM transactions
        GROUP BY status
      `);
      const transactionsByStatus: Record<string, number> = {};
      statusResult.rows.forEach(row => {
        transactionsByStatus[row.status] = parseInt(row.count);
      });

      // Get total validators
      const validatorsResult = await client.query('SELECT COUNT(*) as count FROM validators');
      const totalValidators = parseInt(validatorsResult.rows[0].count);

      // Get total contracts (unique to_addresses with type 1 or deployed contracts)
      const contractsResult = await client.query('SELECT COUNT(*) as count FROM current_state');
      const totalContracts = parseInt(contractsResult.rows[0].count);

      // Get recent transactions
      const recentTxResult = await client.query(`
        SELECT * FROM transactions
        ORDER BY created_at DESC
        LIMIT 10
      `);

      // Get transaction types breakdown (Deploy vs Call)
      // Deploy: type 0 OR (type 1 with contract_code in contract_snapshot)
      // Call: type 1 or 2 without contract_code
      const deployResult = await client.query(`
        SELECT COUNT(*) as count FROM transactions
        WHERE type = 0
           OR (type = 1 AND (
             contract_snapshot->>'contract_code' IS NOT NULL
             OR contract_snapshot->'states'->'finalized'->>'contract_code' IS NOT NULL
             OR contract_snapshot->'states'->'accepted'->>'contract_code' IS NOT NULL
           ))
      `);
      const deployCount = parseInt(deployResult.rows[0].count);
      const callCount = totalTransactions - deployCount;

      const transactionsByType: Record<string, number> = {
        'deploy': deployCount,
        'call': callCount,
      };

      // Get appealed transactions count
      const appealedResult = await client.query(`
        SELECT COUNT(*) as count FROM transactions WHERE appealed = true
      `);
      const appealedTransactions = parseInt(appealedResult.rows[0].count);

      return NextResponse.json({
        totalTransactions,
        transactionsByStatus,
        transactionsByType,
        totalValidators,
        totalContracts,
        appealedTransactions,
        recentTransactions: recentTxResult.rows,
      });
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Database error:', error);
    return NextResponse.json({ error: 'Database connection failed' }, { status: 500 });
  }
}
