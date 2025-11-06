// GenLayer Health Dashboard JavaScript
class HealthDashboard {
    constructor() {
        this.apiUrl = document.getElementById('api-url').value;
        this.refreshInterval = null;
        this.isRefreshing = false;

        this.initializeEventListeners();
        this.startAutoRefresh();
        this.fetchAllMetrics();
    }

    initializeEventListeners() {
        // Refresh interval change
        document.getElementById('refresh-interval').addEventListener('change', (e) => {
            this.startAutoRefresh();
        });

        // API URL change
        document.getElementById('api-url').addEventListener('change', (e) => {
            this.apiUrl = e.target.value;
            this.fetchAllMetrics();
        });

        // Manual refresh button
        document.getElementById('refresh-now').addEventListener('click', () => {
            this.fetchAllMetrics();
        });
    }

    startAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }

        const intervalSeconds = parseInt(document.getElementById('refresh-interval').value);

        if (intervalSeconds > 0) {
            this.refreshInterval = setInterval(() => {
                this.fetchAllMetrics();
            }, intervalSeconds * 1000);
        }
    }

    async fetchAllMetrics() {
        if (this.isRefreshing) return;

        this.isRefreshing = true;

        try {
            // Fetch all health endpoints in parallel
            const [overall, workers, consensus, db, memory, tasks, processing] = await Promise.all([
                this.fetchEndpoint('/health'),
                this.fetchEndpoint('/health/workers'),
                this.fetchEndpoint('/health/consensus'),
                this.fetchEndpoint('/health/db'),
                this.fetchEndpoint('/health/memory'),
                this.fetchEndpoint('/health/tasks'),
                this.fetchEndpoint('/health/processing')
            ]);

            this.renderDashboard({
                overall,
                workers,
                consensus,
                db,
                memory,
                tasks,
                processing
            });

            this.updateLastRefreshTime();
        } catch (error) {
            this.renderError(error);
        } finally {
            this.isRefreshing = false;
        }
    }

    async fetchEndpoint(path) {
        const response = await fetch(`${this.apiUrl}${path}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch ${path}: ${response.statusText}`);
        }
        return response.json();
    }

    updateLastRefreshTime() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString();
        document.getElementById('last-update').textContent = `Last updated: ${timeStr}`;
    }

    renderError(error) {
        const content = document.getElementById('dashboard-content');
        content.innerHTML = `
            <div class="error-message">
                <strong>Error loading metrics:</strong> ${error.message}
                <br><br>
                Please check that the API URL is correct and the backend is running.
            </div>
        `;
    }

    renderDashboard(data) {
        const { overall, workers, consensus, db, memory, tasks, processing } = data;

        // Update overall status badge
        document.getElementById('overall-status').innerHTML =
            `<div class="status-badge status-${overall.status}">${overall.status}</div>`;

        // Render dashboard grid
        const content = document.getElementById('dashboard-content');
        content.innerHTML = `
            <div class="grid">
                ${this.renderOverviewCard(overall)}
                ${this.renderWorkersCard(workers)}
            </div>
            <div class="grid">
                ${this.renderConsensusCard(consensus)}
                ${this.renderDatabaseCard(db)}
            </div>
            <div class="grid">
                ${this.renderMemoryCard(memory)}
                ${this.renderTasksCard(tasks)}
            </div>
            ${processing.processing_count > 0 ? `<div class="grid">${this.renderProcessingCard(processing)}</div>` : ''}
        `;
    }

    renderOverviewCard(data) {
        const services = data.services || {};

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">System Overview</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Response Time</span>
                        <span class="metric-value">${data.response_time_ms?.toFixed(2)} ms</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Database</span>
                        <span class="metric-value">
                            <span class="status-badge status-${services.database?.status || 'unknown'}">${services.database?.status || 'unknown'}</span>
                        </span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Redis</span>
                        <span class="metric-value">
                            <span class="status-badge status-${services.redis}">${services.redis}</span>
                        </span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Consensus Workers</span>
                        <span class="metric-value">${services.consensus_workers?.healthy || 0} / ${services.consensus_workers?.total || 0} healthy</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Processing Transactions</span>
                        <span class="metric-value">${services.consensus?.processing_transactions || 0}</span>
                    </div>
                    ${services.consensus?.orphaned_transactions > 0 ? `
                        <div class="metric-row">
                            <span class="metric-label">Orphaned Transactions</span>
                            <span class="metric-value">
                                <span class="badge badge-danger">${services.consensus.orphaned_transactions}</span>
                            </span>
                        </div>
                    ` : ''}
                    ${data.issues && data.issues.length > 0 ? `
                        <div class="metric-row">
                            <span class="metric-label">Issues</span>
                            <span class="metric-value">
                                ${data.issues.map(issue => `<span class="badge badge-warning">${issue}</span>`).join(' ')}
                            </span>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    renderWorkersCard(data) {
        if (data.status === 'error') {
            return `
                <div class="card">
                    <div class="card-header">
                        <div class="card-title">Consensus Workers</div>
                        <div class="status-badge status-error">Error</div>
                    </div>
                    <div class="card-body">
                        <div class="error-message">${data.error}</div>
                    </div>
                </div>
            `;
        }

        const workers = data.workers || [];
        const jsonrpc = data.jsonrpc;

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Consensus Workers</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Total Workers</span>
                        <span class="metric-value">${data.total_workers || 0}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Healthy Workers</span>
                        <span class="metric-value">${data.healthy_workers || 0}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Avg CPU Usage</span>
                        <span class="metric-value">${data.aggregated_metrics?.avg_cpu_percent?.toFixed(2)}%</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Total Memory</span>
                        <span class="metric-value">${data.aggregated_metrics?.total_memory_mb?.toFixed(2)} MB</span>
                    </div>

                    ${jsonrpc ? `
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155;">
                            <div style="font-weight: 600; margin-bottom: 8px; color: #94a3b8;">JSONRPC Service</div>
                            <div class="metric-row">
                                <span class="metric-label">Status</span>
                                <span class="metric-value">
                                    <span class="status-badge status-${jsonrpc.status}">${jsonrpc.status}</span>
                                </span>
                            </div>
                            <div class="metric-row">
                                <span class="metric-label">CPU Usage</span>
                                <span class="metric-value">${jsonrpc.cpu_percent}%</span>
                            </div>
                            <div class="metric-row">
                                <span class="metric-label">Memory Usage</span>
                                <span class="metric-value">${jsonrpc.memory_mb} MB (${jsonrpc.memory_percent?.toFixed(1)}%)</span>
                            </div>
                        </div>
                    ` : ''}

                    ${workers.length > 0 ? `
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155;">
                            <div style="font-weight: 600; margin-bottom: 8px; color: #94a3b8;">Worker Details</div>
                            <div class="worker-grid">
                                ${workers.map(worker => this.renderWorkerCard(worker)).join('')}
                            </div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    renderWorkerCard(worker) {
        return `
            <div class="worker-card">
                <div class="worker-name">${worker.worker_id || worker.container_name}</div>
                <div class="status-badge status-${worker.status}" style="margin-bottom: 8px; font-size: 10px;">
                    ${worker.status}
                </div>
                <div class="worker-stats">
                    <div class="worker-stat">
                        <span style="color: #94a3b8;">CPU:</span>
                        <span>${worker.cpu_percent}%</span>
                    </div>
                    <div class="worker-stat">
                        <span style="color: #94a3b8;">Memory:</span>
                        <span>${worker.memory_mb?.toFixed(1)} MB</span>
                    </div>
                    ${worker.current_transaction ? `
                        <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #475569;">
                            <div style="color: #60a5fa; font-size: 11px;">Processing TX:</div>
                            <div style="font-family: monospace; font-size: 10px; color: #94a3b8;">
                                ${worker.current_transaction.hash?.substring(0, 16)}...
                            </div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    renderConsensusCard(data) {
        if (data.status === 'error') {
            return `
                <div class="card">
                    <div class="card-header">
                        <div class="card-title">Consensus System</div>
                        <div class="status-badge status-error">Error</div>
                    </div>
                    <div class="card-body">
                        <div class="error-message">${data.error}</div>
                    </div>
                </div>
            `;
        }

        const contracts = data.contracts || [];

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Consensus System</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Active Workers</span>
                        <span class="metric-value">${data.active_workers || 0}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Processing Transactions</span>
                        <span class="metric-value">${data.total_processing_transactions || 0}</span>
                    </div>
                    ${data.total_orphaned_transactions > 0 ? `
                        <div class="metric-row">
                            <span class="metric-label">Orphaned Transactions</span>
                            <span class="metric-value">
                                <span class="badge badge-danger">${data.total_orphaned_transactions}</span>
                            </span>
                        </div>
                    ` : ''}

                    ${contracts.length > 0 ? `
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155;">
                            <div style="font-weight: 600; margin-bottom: 12px; color: #94a3b8;">Contract Activity</div>
                            <table class="contract-table">
                                <thead>
                                    <tr>
                                        <th>Contract</th>
                                        <th>Processing</th>
                                        <th>Pending</th>
                                        <th>Last 1h</th>
                                        <th>Oldest TX</th>
                                        ${data.total_orphaned_transactions > 0 ? '<th>Orphaned</th>' : ''}
                                    </tr>
                                </thead>
                                <tbody>
                                    ${contracts.map(contract => `
                                        <tr>
                                            <td>
                                                <div class="contract-address">${contract.contract_address.substring(0, 12)}...</div>
                                            </td>
                                            <td>${contract.processing_count}</td>
                                            <td>${contract.pending_count}</td>
                                            <td>${contract.created_last_1h}</td>
                                            <td>${contract.oldest_transaction_elapsed || '-'}</td>
                                            ${data.total_orphaned_transactions > 0 ? `
                                                <td>
                                                    ${contract.orphaned_transactions > 0 ?
                                                        `<span class="badge badge-danger">${contract.orphaned_transactions}</span>` :
                                                        '-'
                                                    }
                                                </td>
                                            ` : ''}
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>
                    ` : '<div style="margin-top: 12px; color: #64748b; text-align: center;">No active contract processing</div>'}
                </div>
            </div>
        `;
    }

    renderDatabaseCard(data) {
        const pool = data.connection_pool || {};

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Database</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Query Time</span>
                        <span class="metric-value">${data.query_time_ms?.toFixed(2)} ms</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Active Sessions</span>
                        <span class="metric-value">${data.active_sessions || 0}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Pool Type</span>
                        <span class="metric-value">${pool.class || 'Unknown'}</span>
                    </div>
                    ${pool.size !== undefined ? `
                        <div class="metric-row">
                            <span class="metric-label">Pool Size</span>
                            <span class="metric-value">${pool.size}</span>
                        </div>
                    ` : ''}
                    ${pool.checked_out !== undefined ? `
                        <div class="metric-row">
                            <span class="metric-label">Checked Out</span>
                            <span class="metric-value">${pool.checked_out}</span>
                        </div>
                    ` : ''}
                    ${pool.overflow !== undefined ? `
                        <div class="metric-row">
                            <span class="metric-label">Overflow</span>
                            <span class="metric-value">${pool.overflow}</span>
                        </div>
                    ` : ''}
                    ${pool.available !== undefined ? `
                        <div class="metric-row">
                            <span class="metric-label">Available</span>
                            <span class="metric-value">${pool.available}</span>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    renderMemoryCard(data) {
        const sysMemPercent = data.system_memory?.percent_used || 0;

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Memory Usage</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Process Memory</span>
                        <span class="metric-value">${data.memory_usage_mb?.toFixed(2)} MB (${data.memory_percent?.toFixed(1)}%)</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Virtual Memory</span>
                        <span class="metric-value">${data.virtual_memory_mb?.toFixed(2)} MB</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">GC Objects</span>
                        <span class="metric-value">${(data.gc_objects || 0).toLocaleString()}</span>
                    </div>

                    <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155;">
                        <div style="font-weight: 600; margin-bottom: 8px; color: #94a3b8;">System Memory</div>
                        <div class="metric-row">
                            <span class="metric-label">Total</span>
                            <span class="metric-value">${data.system_memory?.total_mb?.toFixed(2)} MB</span>
                        </div>
                        <div class="metric-row">
                            <span class="metric-label">Available</span>
                            <span class="metric-value">${data.system_memory?.available_mb?.toFixed(2)} MB</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${sysMemPercent}%">
                                ${sysMemPercent.toFixed(1)}%
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    renderTasksCard(data) {
        const staleTaskDetails = data.stale_task_details || [];

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Background Tasks</div>
                    <div class="status-badge status-${data.status}">${data.status}</div>
                </div>
                <div class="card-body">
                    <div class="metric-row">
                        <span class="metric-label">Uptime</span>
                        <span class="metric-value">${this.formatUptime(data.uptime_seconds)}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Active Tasks</span>
                        <span class="metric-value">${data.active_tasks || 0}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Stale Tasks</span>
                        <span class="metric-value">
                            ${data.stale_tasks > 0 ?
                                `<span class="badge badge-warning">${data.stale_tasks}</span>` :
                                '0'
                            }
                        </span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">CPU Usage</span>
                        <span class="metric-value">${data.cpu_percent?.toFixed(2)}%</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Memory Usage</span>
                        <span class="metric-value">${data.memory_usage_mb?.toFixed(2)} MB</span>
                    </div>

                    ${staleTaskDetails.length > 0 ? `
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #334155;">
                            <div style="font-weight: 600; margin-bottom: 8px; color: #f59e0b;">Stale Task Details</div>
                            ${staleTaskDetails.map(task => `
                                <div style="background: #0f172a; padding: 8px; border-radius: 4px; margin: 4px 0; font-size: 12px;">
                                    <div><strong>Task:</strong> ${task.task_name}</div>
                                    <div style="color: #94a3b8;">Idle: ${task.idle_time}</div>
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }

    renderProcessingCard(data) {
        const processing = data.processing_transactions || {};
        const contracts = Object.keys(processing);

        if (contracts.length === 0) {
            return '';
        }

        return `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Current Transaction Processing</div>
                    <div class="badge badge-info">${data.processing_count} processing</div>
                </div>
                <div class="card-body">
                    ${contracts.map(contractAddr => {
                        const txs = processing[contractAddr];
                        return `
                            <div style="margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid #334155;">
                                <div class="contract-address" style="margin-bottom: 8px;">${contractAddr}</div>
                                <div style="font-size: 13px; color: #94a3b8;">
                                    ${txs.length} transaction${txs.length !== 1 ? 's' : ''} processing
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    formatUptime(seconds) {
        if (!seconds) return '0s';

        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        const parts = [];
        if (days > 0) parts.push(`${days}d`);
        if (hours > 0) parts.push(`${hours}h`);
        if (minutes > 0) parts.push(`${minutes}m`);
        if (secs > 0 || parts.length === 0) parts.push(`${secs}s`);

        return parts.join(' ');
    }
}

// Initialize dashboard when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        new HealthDashboard();
    });
} else {
    new HealthDashboard();
}
