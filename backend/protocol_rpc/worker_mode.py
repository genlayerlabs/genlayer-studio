# backend/protocol_rpc/worker_mode.py
"""
Worker mode detection and configuration for jsonrpc service
"""
import os
import logging

logger = logging.getLogger(__name__)

class WorkerModeConfig:
    """Configuration based on READER_WORKERS and WRITER_WORKERS environment variables"""
    
    def __init__(self):
        self.reader_workers = int(os.getenv('READER_WORKERS', '0'))
        self.writer_workers = int(os.getenv('WRITER_WORKERS', '0'))
        
        # Determine actual mode
        self._determine_mode()
        
    def _determine_mode(self):
        """Determine the worker mode based on configuration"""
        if self.reader_workers == 0 and self.writer_workers == 0:
            self.mode = 'simple-rpc'
            self.description = 'Infrastructure node (simple RPC, no GenVM)'
            self.has_genvm = False
            self.handles_consensus = False
            
        elif self.reader_workers > 0 and self.writer_workers == 0:
            self.mode = 'reader'
            self.description = f'Read farm ({self.reader_workers} replicas with GenVM)'
            self.has_genvm = True
            self.handles_consensus = False
            
        elif self.reader_workers == 0 and self.writer_workers > 0:
            self.mode = 'simple-rpc'
            self.description = f'Write farm coordinator (simple RPC + {self.writer_workers} consensus workers)'
            self.has_genvm = False
            self.handles_consensus = False  # Consensus handled by separate workers
            
        else:  # Both > 0
            self.mode = 'hybrid'
            self.description = f'Hybrid node ({self.reader_workers} readers, {self.writer_workers} writers)'
            self.has_genvm = True
            self.handles_consensus = True
        
        logger.info(f"Worker mode configured: {self.mode} - {self.description}")
    
    def should_handle_genvm_read(self) -> bool:
        """Check if this worker should handle GenVM read operations"""
        return self.has_genvm and self.reader_workers > 0
    
    def should_handle_simple_rpc(self) -> bool:
        """Check if this worker should handle simple RPC calls"""
        return self.reader_workers == 0 or self.mode == 'hybrid'
    
    def should_queue_writes_to_zmq(self) -> bool:
        """Check if writes should be queued to ZeroMQ"""
        consensus_mode = os.getenv('CONSENSUS_MODE', 'legacy')
        return consensus_mode in ['zmq', 'hybrid'] and self.writer_workers > 0
    
    def get_endpoints_to_disable(self) -> list:
        """Get list of endpoints to disable based on mode"""
        disabled = []
        
        if self.mode == 'simple-rpc':
            # Disable GenVM read endpoints
            disabled.extend([
                'call',
                'staticcall',
                'eth_call',
                'eth_estimateGas'
            ])
            logger.info("Disabling GenVM read endpoints in simple-rpc mode")
            
        elif self.mode == 'reader':
            # Disable write/transaction endpoints
            disabled.extend([
                'sendTransaction',
                'eth_sendTransaction',
                'eth_sendRawTransaction'
            ])
            logger.info("Disabling write endpoints in reader mode")
        
        return disabled
    
    def get_webdriver_requirement(self) -> bool:
        """Check if WebDriver is required"""
        return self.has_genvm
    
    def get_status(self) -> dict:
        """Get current worker mode status"""
        return {
            'mode': self.mode,
            'description': self.description,
            'reader_workers': self.reader_workers,
            'writer_workers': self.writer_workers,
            'has_genvm': self.has_genvm,
            'handles_consensus': self.handles_consensus,
            'webdriver_required': self.get_webdriver_requirement(),
            'disabled_endpoints': self.get_endpoints_to_disable()
        }


# Global instance
worker_config = WorkerModeConfig()


def is_genvm_endpoint(endpoint: str) -> bool:
    """Check if an endpoint requires GenVM"""
    genvm_endpoints = [
        'call',
        'staticcall', 
        'eth_call',
        'eth_estimateGas'
    ]
    return any(genvm_ep in endpoint.lower() for genvm_ep in genvm_endpoints)


def should_handle_endpoint(endpoint: str) -> bool:
    """Check if this worker should handle the given endpoint"""
    global worker_config
    
    if is_genvm_endpoint(endpoint):
        return worker_config.should_handle_genvm_read()
    else:
        return worker_config.should_handle_simple_rpc()


def get_worker_info() -> dict:
    """Get worker information for monitoring"""
    global worker_config
    return worker_config.get_status()