import { Pool } from 'pg';

let pool: Pool | null = null;

function createPool(): Pool {
  if (!pool) {
    pool = new Pool({
      host: process.env.DB_HOST,
      database: process.env.DB_NAME,
      user: process.env.DB_USER,
      password: process.env.DB_PASSWORD,
      port: parseInt(process.env.DB_PORT || '5432', 10),
      max: parseInt(process.env.DB_MAX || '10', 10),
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 10000,
    });
  }
  return pool;
}

// Use a proxy to lazily initialize the pool only when accessed
const poolProxy = new Proxy({} as Pool, {
  get(_target, prop) {
    const actualPool = createPool();
    return (actualPool as unknown as Record<string | symbol, unknown>)[prop];
  },
});

export default poolProxy;
