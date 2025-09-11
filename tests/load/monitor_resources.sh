#!/bin/bash

# Resource monitoring script for load tests
# Logs CPU and memory usage to a CSV file

# Parse arguments
ACTION="${1:-start}"
LOG_FILE="${2:-resource_usage.csv}"
INTERVAL="${3:-1}"  # Default 1 second between samples

# PID file to track monitoring process
PID_FILE=".monitor_pid"

case "$ACTION" in
    start)
        echo "Starting resource monitoring..."
        echo "Logging to: $LOG_FILE"
        echo "Sample interval: ${INTERVAL}s"

        # Create CSV header
        echo "timestamp,cpu_percent,memory_percent,memory_used_mb,memory_available_mb,load_1min,load_5min,load_15min" > "$LOG_FILE"

        # Start monitoring in background
        (
            while true; do
                TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

                # Get system stats based on OS
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    # macOS
                    # CPU usage (using ps to get overall CPU)
                    CPU_PERCENT=$(ps aux | awk '{sum+=$3} END {printf "%.1f", sum}')

                    # Memory stats using vm_stat
                    VM_STAT=$(vm_stat)
                    PAGES_FREE=$(echo "$VM_STAT" | grep "Pages free" | awk '{print $3}' | sed 's/\.//')
                    PAGES_ACTIVE=$(echo "$VM_STAT" | grep "Pages active" | awk '{print $3}' | sed 's/\.//')
                    PAGES_INACTIVE=$(echo "$VM_STAT" | grep "Pages inactive" | awk '{print $3}' | sed 's/\.//')
                    PAGES_WIRED=$(echo "$VM_STAT" | grep "Pages wired" | awk '{print $4}' | sed 's/\.//')
                    PAGES_COMPRESSED=$(echo "$VM_STAT" | grep "Pages compressed" | awk '{print $3}' | sed 's/\.//')

                    # Page size is typically 4096 bytes on macOS
                    PAGE_SIZE=4096

                    # Calculate memory in MB
                    MEMORY_FREE_MB=$((PAGES_FREE * PAGE_SIZE / 1024 / 1024))
                    MEMORY_USED_MB=$(((PAGES_ACTIVE + PAGES_INACTIVE + PAGES_WIRED + PAGES_COMPRESSED) * PAGE_SIZE / 1024 / 1024))
                    MEMORY_TOTAL_MB=$((MEMORY_USED_MB + MEMORY_FREE_MB))
                    MEMORY_PERCENT=$(echo "scale=1; $MEMORY_USED_MB * 100 / $MEMORY_TOTAL_MB" | bc 2>/dev/null || echo "0")

                    # Load averages
                    LOAD_AVG=$(sysctl -n vm.loadavg | awk '{print $2","$3","$4}')

                else
                    # Linux
                    # CPU usage from top
                    CPU_PERCENT=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1}')

                    # Memory stats from free
                    MEM_INFO=$(free -m | grep "^Mem:")
                    MEMORY_TOTAL_MB=$(echo "$MEM_INFO" | awk '{print $2}')
                    MEMORY_USED_MB=$(echo "$MEM_INFO" | awk '{print $3}')
                    MEMORY_FREE_MB=$(echo "$MEM_INFO" | awk '{print $4}')
                    MEMORY_PERCENT=$(echo "scale=1; $MEMORY_USED_MB * 100 / $MEMORY_TOTAL_MB" | bc)

                    # Load averages
                    LOAD_AVG=$(uptime | awk -F'load average:' '{print $2}' | sed 's/ //g')
                fi

                # Write to CSV
                echo "$TIMESTAMP,$CPU_PERCENT,$MEMORY_PERCENT,$MEMORY_USED_MB,$MEMORY_FREE_MB,$LOAD_AVG" >> "$LOG_FILE"

                sleep "$INTERVAL"
            done
        ) &

        # Save PID
        echo $! > "$PID_FILE"
        echo "Monitor started with PID: $(cat "$PID_FILE")"
        ;;

    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            echo "Stopping monitor (PID: $PID)..."
            kill "$PID" 2>/dev/null || true
            rm -f "$PID_FILE"

            # Generate summary
            if [ -f "$LOG_FILE" ]; then
                echo ""
                echo "=== Resource Usage Summary ==="
                echo "Log file: $LOG_FILE"

                # Calculate stats (skip header)
                tail -n +2 "$LOG_FILE" | awk -F',' '
                    BEGIN {
                        cpu_sum = 0; cpu_max = 0; cpu_count = 0;
                        mem_sum = 0; mem_max = 0; mem_count = 0;
                    }
                    {
                        cpu_count++;
                        cpu_sum += $2;
                        if ($2 > cpu_max) cpu_max = $2;

                        mem_count++;
                        mem_sum += $3;
                        if ($3 > mem_max) mem_max = $3;
                    }
                    END {
                        if (cpu_count > 0) {
                            printf "CPU Usage:    Avg: %.1f%%, Max: %.1f%%\n", cpu_sum/cpu_count, cpu_max;
                        }
                        if (mem_count > 0) {
                            printf "Memory Usage: Avg: %.1f%%, Max: %.1f%%\n", mem_sum/mem_count, mem_max;
                        }
                        printf "Total samples: %d\n", cpu_count;
                    }
                '
                echo "============================="
            fi
        else
            echo "No monitor running (PID file not found)"
        fi
        ;;

    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "Monitor is running (PID: $PID)"
                if [ -f "$LOG_FILE" ]; then
                    echo "Latest reading:"
                    tail -n 1 "$LOG_FILE"
                fi
            else
                echo "Monitor PID file exists but process is not running"
                rm -f "$PID_FILE"
            fi
        else
            echo "Monitor is not running"
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|status} [log_file] [interval_seconds]"
        echo "  start  - Start monitoring"
        echo "  stop   - Stop monitoring and show summary"
        echo "  status - Check if monitor is running"
        exit 1
        ;;
esac