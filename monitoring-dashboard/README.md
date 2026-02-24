# GenLayer System Health Monitoring Dashboard

A standalone, decoupled monitoring dashboard for the GenLayer system health metrics.

## Features

- **Real-time Monitoring**: Auto-refreshing health metrics (5s - 60s intervals)
- **System Overview**: Overall health status with database, Redis, and consensus status
- **Worker Monitoring**: Individual consensus worker metrics (CPU, memory, current transactions)
- **Consensus Tracking**: Transaction processing grouped by contract address
- **Database Health**: Connection pool statistics and query performance
- **Memory Analytics**: Process and system memory usage with GC stats
- **Background Tasks**: Task monitoring with stale task detection

## Architecture

This dashboard is completely decoupled from the main GenLayer Studio frontend:

- **No shared dependencies**: Standalone HTML/CSS/JavaScript
- **Direct API calls**: Fetches metrics directly from backend health endpoints
- **Self-contained**: All styles and logic embedded in two files

## Files

- `index.html` - Dashboard UI with embedded CSS
- `dashboard.js` - Data fetching and rendering logic
- `README.md` - This file

## Usage

### Local Development

The dashboard is automatically served by the FastAPI backend at:

```
http://localhost:4000/monitoring
```

### Configuration

You can configure:

- **API URL**: Change the backend API URL (default: `http://localhost:4000`)
- **Refresh Interval**: Choose between 5s, 10s, 30s, 60s, or manual refresh

### Health Endpoints

The dashboard fetches from these backend endpoints:

- `GET /health` - Overall system health summary
- `GET /health/workers` - Consensus worker details
- `GET /health/consensus` - Transaction processing by contract
- `GET /health/db` - Database connection pool stats
- `GET /health/memory` - Memory usage metrics
- `GET /health/tasks` - Background task monitoring
- `GET /health/processing` - Current transaction processing

## Status Indicators

- **Green (healthy)**: All systems operating normally
- **Yellow (degraded)**: Some issues detected but system operational
- **Red (unhealthy/error)**: Critical issues requiring attention
- **Gray (unknown)**: Status could not be determined

## Docker Deployment

The monitoring dashboard is automatically included in the backend Docker image and served at `/monitoring` path.

No additional configuration needed - just access:

```
http://<your-backend-host>:4000/monitoring
```

## Development Notes

This dashboard is intentionally simple and dependency-free:

- No build process required
- No npm packages
- No framework dependencies
- Pure vanilla JavaScript + HTML + CSS

This ensures it remains operational even if the main Studio frontend has issues.
