#!/bin/bash

# Base log directory
LOG_BASE="/var/log/ansible"

# Create date & time based folder
TODAY=$(date +%Y-%m-%d)
NOW=$(date +%H-%M-%S)
LOG_DIR="$LOG_BASE/$TODAY/$NOW"
VERSION=$1 
DUT_IP=$2
VENDOR=$3
MODEL=$4
DOWNLOAD_LATEST=$5
MANUAL_FILENAME=$6  # Optional manual filename parameter

mkdir -p "$LOG_DIR"

# Log file name
LOG_FILE="$LOG_DIR/upgrade_run.log"

# Create hostname from model (lowercase, replace spaces with dashes)
HOSTNAME=$(echo "$MODEL" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

# Create hostname-specific directory
mkdir -p "$LOG_DIR/$HOSTNAME"
echo "Created directory for host: $HOSTNAME at $LOG_DIR/$HOSTNAME"

# Create dynamic inventory file with the DUT IP
INVENTORY_FILE="$LOG_DIR/dynamic_inventory.yml"

echo "Creating dynamic inventory file at: $INVENTORY_FILE"

cat > "$INVENTORY_FILE" << EOF
all:
  children:
    vos:
      hosts:
        ${HOSTNAME}:
          ansible_host: ${DUT_IP}
          ansible_ssh_user: admin
          ansible_ssh_pass: versa123
          ansible_become: yes
          ansible_become_pass: versa123
EOF

# Log the execution parameters
echo "=================================================="
echo "Ansible Execution Parameters:"
echo "Version: $VERSION"
echo "DUT IP: $DUT_IP"
echo "Vendor: $VENDOR"
echo "Model: $MODEL"
echo "Hostname: $HOSTNAME"
echo "Download Latest: $DOWNLOAD_LATEST"
if [ -n "$MANUAL_FILENAME" ]; then
    echo "Manual Filename: $MANUAL_FILENAME"
fi
echo "Log Directory: $LOG_DIR"
echo "Inventory File: $INVENTORY_FILE"
echo "=================================================="

# Check if Python and dependencies are available
echo "Checking Python environment..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 is not installed!"
    exit 1
fi

# Check for required Python packages if downloading
if [ "$DOWNLOAD_LATEST" = "true" ]; then
    echo "Verifying Python dependencies for download..."
    python3 -c "import requests, bs4" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "Installing Python dependencies (requests, beautifulsoup4)..."
        pip3 install requests beautifulsoup4 || {
            echo "ERROR: Failed to install Python dependencies"
            exit 1
        }
    fi
    echo "Python dependencies verified"
fi

# Prepare ansible-playbook command
EXTRA_VARS="build_version=$VERSION"

# Add download_latest flag if downloading from builds.versa-networks.com
if [ "$DOWNLOAD_LATEST" = "true" ]; then
    EXTRA_VARS="$EXTRA_VARS download_latest=true"
    echo "Will download latest image from builds.versa-networks.com for version $VERSION"
    
    # Add manual filename if provided
    if [ -n "$MANUAL_FILENAME" ]; then
        EXTRA_VARS="$EXTRA_VARS manual_filename=$MANUAL_FILENAME"
        echo "Using manual filename: $MANUAL_FILENAME"
    fi
fi

# Build the ansible command
ANSIBLE_CMD="ansible-playbook /home/versa/git/ansible_automation/Upgrade_Testing/run_upgrade.yml -i $INVENTORY_FILE -e \"$EXTRA_VARS\""

echo "Running command: $ANSIBLE_CMD"
echo "=================================================="

# Execute the ansible command
eval "$ANSIBLE_CMD" | tee "$LOG_FILE"

# Get the exit code from ansible-playbook
ANSIBLE_EXIT_CODE=${PIPESTATUS[0]}

# Function to organize logs by hostname
organize_ansible_logs() {
    local main_log="$1"
    local log_dir="$2"
    local hostname="$3"
    
    if [ ! -f "$main_log" ]; then
        echo "Main log file not found: $main_log"
        return 1
    fi
    
    echo "Organizing logs for hostname: $hostname"
    
    if [ -d "$log_dir/$hostname" ]; then
        hostname_log="$log_dir/$hostname/${hostname}_upgrade.log"
        hostname_summary="$log_dir/$hostname/${hostname}_summary.txt"
        
        # Extract logs for this hostname
        grep -E "(TASK \[|$hostname|PLAY \[|PLAY RECAP|ERROR|FAILED|DOWNLOAD|SUCCESS)" "$main_log" > "$hostname_log"
        
        # Create summary file
        echo "=== Upgrade Summary for $hostname ===" > "$hostname_summary"
        echo "Timestamp: $(date)" >> "$hostname_summary"
        echo "Version: $VERSION" >> "$hostname_summary"
        echo "DUT IP: $DUT_IP" >> "$hostname_summary"
        echo "Vendor: $VENDOR" >> "$hostname_summary"
        echo "Model: $MODEL" >> "$hostname_summary"
        echo "Download Latest: $DOWNLOAD_LATEST" >> "$hostname_summary"
        if [ -n "$MANUAL_FILENAME" ]; then
            echo "Manual Filename: $MANUAL_FILENAME" >> "$hostname_summary"
        fi
        echo "" >> "$hostname_summary"
        
        # Extract statistics
        task_count=$(grep -c "TASK \[" "$hostname_log" || echo "0")
        ok_count=$(grep -c "ok: \[$hostname\]" "$main_log" || echo "0")
        changed_count=$(grep -c "changed: \[$hostname\]" "$main_log" || echo "0")
        failed_count=$(grep -c "failed: \[$hostname\]" "$main_log" || echo "0")
        download_status=$(grep -i "DOWNLOAD.*for $hostname" "$main_log" | tail -1 || echo "N/A")
        
        echo "Tasks executed: $task_count" >> "$hostname_summary"
        echo "Successful tasks: $ok_count" >> "$hostname_summary"
        echo "Changed tasks: $changed_count" >> "$hostname_summary"
        echo "Failed tasks: $failed_count" >> "$hostname_summary"
        echo "" >> "$hostname_summary"
        echo "Download Status: $download_status" >> "$hostname_summary"
        
        # Check for final status in logs
        if grep -q "VALIDATION SUCCESS" "$main_log"; then
            echo "Final Result: SUCCESS - All validation checks passed" >> "$hostname_summary"
        elif grep -q "VALIDATION FAILED" "$main_log"; then
            echo "Final Result: FAILED - Validation checks failed" >> "$hostname_summary"
        else
            echo "Final Result: INCOMPLETE - Check logs for details" >> "$hostname_summary"
        fi
        
        echo "Created log and summary for $hostname"
    fi
}

# Organize logs after ansible completes
organize_ansible_logs "$LOG_FILE" "$LOG_DIR" "$HOSTNAME"

echo ""
echo "=================================================="
echo "Ansible playbook execution completed!"
echo "Exit code: $ANSIBLE_EXIT_CODE"
echo "Log file: $LOG_FILE"
echo "Hostname-specific logs created in: $LOG_DIR/$HOSTNAME"
echo "Summary file: $LOG_DIR/$HOSTNAME/${HOSTNAME}_summary.txt"
echo "=================================================="

# Display summary if it exists
if [ -f "$LOG_DIR/$HOSTNAME/${HOSTNAME}_summary.txt" ]; then
    echo ""
    echo "=== Quick Summary ==="
    cat "$LOG_DIR/$HOSTNAME/${HOSTNAME}_summary.txt"
fi

exit $ANSIBLE_EXIT_CODE