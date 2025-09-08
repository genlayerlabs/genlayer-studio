# backend/protocol_rpc/health.py
from flask import Blueprint, jsonify
import time
import os

health_bp = Blueprint('health', __name__)

@health_bp.route('/health')
def health_check():
    """Simple health check endpoint for load balancers and monitoring"""
    start = time.time()
    
    # Basic health check - we're running
    status = "healthy"
    
    # Check database connectivity if possible
    db_status = "unknown"
    try:
        from flask import current_app
        from sqlalchemy import text
        if hasattr(current_app, 'extensions') and 'sqlalchemy' in current_app.extensions:
            db = current_app.extensions['sqlalchemy']
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
        status = "degraded"
    
    # Check Redis (if configured)
    redis_status = "not_configured"
    if os.getenv('REDIS_URL'):
        try:
            import redis
            redis_client = redis.from_url(os.getenv('REDIS_URL'))
            redis_client.ping()
            redis_status = "healthy"
        except Exception:
            redis_status = "unhealthy"
    
    # System metrics (optional)
    metrics = {}
    try:
        import psutil
        metrics = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
        }
    except ImportError:
        pass
    
    return jsonify({
        "status": status,
        "database": db_status,
        "redis": redis_status,
        "response_time_ms": (time.time() - start) * 1000,
        "worker_pid": os.getpid(),
        "workers": os.getenv('WEB_CONCURRENCY', '1'),
        **metrics
    })

@health_bp.route('/ready')
def readiness_check():
    """Readiness check to verify the service is ready to accept traffic"""
    # Could add additional checks here
    return jsonify({
        'status': 'ready',
        'service': 'genlayer-rpc'
    }), 200