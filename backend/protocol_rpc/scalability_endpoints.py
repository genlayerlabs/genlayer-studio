# backend/protocol_rpc/scalability_endpoints.py
"""
Monitoring and management endpoints for the scalability infrastructure
"""
import os
import time
import requests
from flask import Blueprint, jsonify
from typing import Dict, Any
import redis

scalability_bp = Blueprint('scalability', __name__)

def get_consensus_mode() -> str:
    """Get current consensus mode"""
    return os.getenv('CONSENSUS_MODE', 'legacy')

def get_zmq_broker_status() -> Dict[str, Any]:
    """Get status from ZeroMQ broker"""
    broker_host = os.getenv('ZMQ_BROKER_HOST', 'localhost')
    try:
        response = requests.get(f'http://{broker_host}:5561/metrics', timeout=2)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        return {'error': str(e), 'connected': False}
    return {'connected': False}

def get_redis_status() -> Dict[str, Any]:
    """Get Redis cache status"""
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    
    try:
        client = redis.Redis(host=redis_host, port=redis_port, socket_connect_timeout=1)
        client.ping()
        info = client.info('stats')
        
        # Count cached items
        snapshots = len(client.keys('snapshot:*'))
        locks = len(client.keys('lock:*'))
        
        return {
            'connected': True,
            'host': redis_host,
            'cached_snapshots': snapshots,
            'active_locks': locks,
            'keyspace_hits': info.get('keyspace_hits', 0),
            'keyspace_misses': info.get('keyspace_misses', 0),
            'hit_rate': (
                info.get('keyspace_hits', 0) / 
                max(1, info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0))
            ) * 100
        }
    except Exception as e:
        return {'connected': False, 'error': str(e)}

def get_queue_status() -> Dict[str, Any]:
    """Get queue status from consensus algorithm"""
    # This would need to be integrated with the ConsensusAlgorithm instance
    # For now, return basic info
    mode = get_consensus_mode()
    
    result = {
        'mode': mode,
        'legacy_queues': 0,  # Would need access to ConsensusAlgorithm.pending_queues
        'zmq_queues': 0
    }
    
    if mode in ['hybrid', 'zmq']:
        zmq_status = get_zmq_broker_status()
        if 'queued_transactions' in zmq_status:
            result['zmq_queues'] = zmq_status['queued_transactions']
    
    return result

@scalability_bp.route('/api/metrics/consensus', methods=['GET'])
def consensus_metrics():
    """Get comprehensive consensus metrics"""
    mode = get_consensus_mode()
    
    metrics = {
        'consensus_mode': mode,
        'timestamp': time.time()
    }
    
    # Add ZeroMQ metrics if applicable
    if mode in ['hybrid', 'zmq']:
        metrics['zmq_broker'] = get_zmq_broker_status()
        metrics['redis_cache'] = get_redis_status()
    
    # Add queue metrics
    metrics['queues'] = get_queue_status()
    
    return jsonify(metrics)

@scalability_bp.route('/api/metrics/scalability', methods=['GET'])
def scalability_status():
    """Get scalability infrastructure status"""
    return jsonify({
        'mode': get_consensus_mode(),
        'services': {
            'zmq_broker': get_zmq_broker_status(),
            'redis': get_redis_status()
        },
        'configuration': {
            'zmq_broker_host': os.getenv('ZMQ_BROKER_HOST', 'localhost'),
            'redis_host': os.getenv('REDIS_HOST', 'localhost'),
            'max_genvm_per_worker': int(os.getenv('MAX_GENVM_PER_WORKER', 5)),
            'writer_workers': int(os.getenv('WRITER_WORKERS', 0)),
            'reader_workers': int(os.getenv('READER_WORKERS', 1))
        }
    })

@scalability_bp.route('/api/health/scalability', methods=['GET'])
def scalability_health():
    """Health check for scalability services"""
    mode = get_consensus_mode()
    
    if mode == 'legacy':
        return jsonify({'status': 'healthy', 'mode': 'legacy'})
    
    # Check critical services for scalability modes
    health_status = {
        'mode': mode,
        'status': 'healthy',
        'services': {}
    }
    
    # Check ZeroMQ broker
    zmq_status = get_zmq_broker_status()
    health_status['services']['zmq_broker'] = zmq_status.get('connected', False)
    
    # Check Redis
    redis_status = get_redis_status()
    health_status['services']['redis'] = redis_status.get('connected', False)
    
    # Determine overall health
    if mode == 'zmq' and not health_status['services']['zmq_broker']:
        health_status['status'] = 'unhealthy'
    elif mode == 'hybrid' and not health_status['services']['zmq_broker']:
        health_status['status'] = 'degraded'
    
    return jsonify(health_status), 200 if health_status['status'] == 'healthy' else 503