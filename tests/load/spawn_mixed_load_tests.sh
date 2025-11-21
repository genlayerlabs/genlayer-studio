#!/bin/bash

# Script to spawn multiple instances of test_mixed_load.sh in the background
# Usage: ./spawn_mixed_load_tests.sh [number_of_instances] [api_url]

# Parse arguments
NUM_INSTANCES=${1:-10}  # Default to 10 instances
API_URL=${2:-"http://localhost:4000/api"}  # Default API URL
MONITOR_FLAG=${3:-""}  # Optional monitor flag

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate that test_mixed_load.sh exists
if [ ! -f "$SCRIPT_DIR/test_mixed_load.sh" ]; then
    echo "❌ Error: test_mixed_load.sh not found in $SCRIPT_DIR"
    exit 1
fi

# Make sure test_mixed_load.sh is executable
chmod +x "$SCRIPT_DIR/test_mixed_load.sh"

echo "==================================================="
echo "     SPAWNING MULTIPLE MIXED LOAD TEST INSTANCES"
echo "==================================================="
echo "Number of instances: $NUM_INSTANCES"
echo "API URL: $API_URL"
echo "Monitor: $(if [ "$MONITOR_FLAG" == "monitor" ]; then echo "ENABLED"; else echo "DISABLED"; fi)"
echo ""

# Create a directory for logs
LOG_DIR="$SCRIPT_DIR/spawn_logs_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
echo "Log directory: $LOG_DIR"
echo ""

# Track PIDs
PIDS=()

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "=== Stopping all test instances ==="
    for pid in "${PIDS[@]}"; do
        if ps -p $pid > /dev/null 2>&1; then
            echo "Killing process $pid..."
            kill $pid 2>/dev/null || true
        fi
    done

    # Wait a bit for processes to terminate
    sleep 2

    # Force kill if still running
    for pid in "${PIDS[@]}"; do
        if ps -p $pid > /dev/null 2>&1; then
            echo "Force killing process $pid..."
            kill -9 $pid 2>/dev/null || true
        fi
    done

    echo "All test instances stopped"
}

# Set up trap to cleanup on script exit
trap cleanup EXIT INT TERM

# Spawn instances
echo "=== Spawning test instances ==="
for i in $(seq 1 $NUM_INSTANCES); do
    LOG_FILE="$LOG_DIR/instance_${i}.log"

    echo -n "Starting instance $i... "

    # Run test_mixed_load.sh in background, redirecting output to log file
    if [ "$MONITOR_FLAG" == "monitor" ] && [ $i -eq 1 ]; then
        # Only enable monitoring for the first instance to avoid conflicts
        "$SCRIPT_DIR/test_mixed_load.sh" "$API_URL" monitor > "$LOG_FILE" 2>&1 &
    else
        "$SCRIPT_DIR/test_mixed_load.sh" "$API_URL" > "$LOG_FILE" 2>&1 &
    fi

    PID=$!
    PIDS+=($PID)

    echo "✅ Started (PID: $PID, Log: $LOG_FILE)"

    # Small delay between spawns to avoid overwhelming the system at startup
    sleep 0.5
done

echo ""
echo "=== All instances spawned ==="
echo "Total instances running: ${#PIDS[@]}"
echo ""

# Function to check status of all instances
check_status() {
    local running=0
    local stopped=0

    for pid in "${PIDS[@]}"; do
        if ps -p $pid > /dev/null 2>&1; then
            ((running++))
        else
            ((stopped++))
        fi
    done

    echo "Status: $running running, $stopped stopped"
}

# Monitor the instances
echo "=== Monitoring instances ==="
echo "Press Ctrl+C to stop all instances"
echo ""

# Initial status
check_status

# Wait for user interrupt or monitor instances
while true; do
    sleep 10

    # Check if any instances have stopped
    local all_running=true
    for pid in "${PIDS[@]}"; do
        if ! ps -p $pid > /dev/null 2>&1; then
            all_running=false
            break
        fi
    done

    if [ "$all_running" = false ]; then
        echo ""
        echo "⚠️  Some instances have stopped"
        check_status

        echo ""
        echo "Do you want to:"
        echo "1) Continue with remaining instances"
        echo "2) Stop all instances and exit"
        echo "3) View logs"
        read -t 10 -n 1 -p "Choice (1/2/3): " choice || choice="1"
        echo ""

        case $choice in
            2)
                echo "Stopping all instances..."
                break
                ;;
            3)
                echo "Recent logs from stopped instances:"
                for pid in "${PIDS[@]}"; do
                    if ! ps -p $pid > /dev/null 2>&1; then
                        for i in $(seq 1 $NUM_INSTANCES); do
                            if [ "${PIDS[$((i-1))]}" == "$pid" ]; then
                                echo ""
                                echo "=== Instance $i (PID: $pid) - Last 20 lines ==="
                                tail -n 20 "$LOG_DIR/instance_${i}.log"
                                break
                            fi
                        done
                    fi
                done
                ;;
            *)
                echo "Continuing with remaining instances..."
                ;;
        esac
    else
        # All instances still running - show periodic status
        echo -n "."
    fi
done

echo ""
echo "=== Test completed ==="
echo "Logs saved in: $LOG_DIR"

# Show summary of each instance
echo ""
echo "=== Instance Summary ==="
for i in $(seq 1 $NUM_INSTANCES); do
    LOG_FILE="$LOG_DIR/instance_${i}.log"
    if [ -f "$LOG_FILE" ]; then
        echo -n "Instance $i: "
        if grep -q "✅ Mixed load test completed successfully!" "$LOG_FILE"; then
            echo "✅ Completed successfully"
        elif grep -q "❌" "$LOG_FILE"; then
            ERRORS=$(grep -c "❌" "$LOG_FILE")
            echo "⚠️  Completed with $ERRORS errors"
        else
            echo "❌ Did not complete"
        fi
    fi
done

echo ""
echo "==================================================="
echo "          ALL INSTANCES FINISHED"
echo "==================================================="