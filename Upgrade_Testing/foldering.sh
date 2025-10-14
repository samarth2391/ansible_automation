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
USERNAME=${6:-admin}
PASSWORD=${7:-versa123}

mkdir -p "$LOG_DIR"

# Log file name
LOG_FILE="$LOG_DIR/upgrade_run.log"

# Create hostname from model (lowercase, replace spaces with dashes)
HOSTNAME=$(echo "$MODEL" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

# Create hostname-specific directory
mkdir -p "$LOG_DIR/$HOSTNAME"
echo "Created directory for host: $HOSTNAME at $LOG_DIR/$HOSTNAME"

# Log the execution parameters
echo "=================================================="
echo "Ansible Execution Parameters:"
echo "Version: $VERSION"
echo "DUT IP: $DUT_IP"
echo "Vendor: $VENDOR"
echo "Model: $MODEL"
echo "Hostname: $HOSTNAME"
echo "Username: $USERNAME"
echo "Download Latest: $DOWNLOAD_LATEST"
echo "Log Directory: $LOG_DIR"
echo "=================================================="

# SSH Setup - Remove old host key and establish new connection
echo "=================================================="
echo "Setting up SSH connection to $DUT_IP..."
echo "=================================================="

# Remove old host key if it exists
if [ -f "$HOME/.ssh/known_hosts" ]; then
    echo "Removing old host key for $DUT_IP from known_hosts..."
    ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$DUT_IP" 2>/dev/null || true
fi

# Test SSH connection and add new host key
echo "Testing SSH connection to $DUT_IP..."
sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 ${USERNAME}@${DUT_IP} "echo 'SSH connection successful'" 2>&1

if [ $? -eq 0 ]; then
    echo "✓ SSH connection to $DUT_IP established successfully"
    
    # Now add the host key properly to known_hosts
    echo "Adding host key to known_hosts..."
    ssh-keyscan -H $DUT_IP >> $HOME/.ssh/known_hosts 2>/dev/null
    
    echo "✓ Host key added to known_hosts"
else
    echo "✗ Failed to establish SSH connection to $DUT_IP"
    echo "Please check:"
    echo "  - IP address is reachable: ping $DUT_IP"
    echo "  - SSH service is running on the device"
    echo "  - Username and password are correct"
    exit 1
fi

echo "=================================================="

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
          ansible_ssh_user: ${USERNAME}
          ansible_ssh_pass: ${PASSWORD}
          ansible_become: yes
          ansible_become_pass: ${PASSWORD}
          ansible_ssh_common_args: '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
EOF

# Prepare ansible-playbook command
EXTRA_VARS="build_version=$VERSION"

# Add download_latest flag if downloading from builds.versa-networks.com
if [ "$DOWNLOAD_LATEST" = "true" ]; then
    EXTRA_VARS="$EXTRA_VARS download_latest=true"
    echo "Will download latest image from builds.versa-networks.com for version $VERSION"
fi

# Build the ansible command
ANSIBLE_CMD="ansible-playbook /home/versa/git/ansible_automation/Upgrade_Testing/run_upgrade.yml -i $INVENTORY_FILE -e \"$EXTRA_VARS\""

echo "Running command: $ANSIBLE_CMD"

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
        grep -E "(TASK \[|$hostname|PLAY \[|PLAY RECAP|ERROR|FAILED)" "$main_log" > "$hostname_log"
        
        # Create summary file
        echo "=== Upgrade Summary for $hostname ===" > "$hostname_summary"
        echo "Timestamp: $(date)" >> "$hostname_summary"
        echo "Version: $VERSION" >> "$hostname_summary"
        echo "DUT IP: $DUT_IP" >> "$hostname_summary"
        echo "Vendor: $VENDOR" >> "$hostname_summary"
        echo "Model: $MODEL" >> "$hostname_summary"
        echo "" >> "$hostname_summary"
        
        # Extract statistics
        task_count=$(grep -c "TASK \[" "$hostname_log" || echo "0")
        ok_count=$(grep -c "ok: \[$hostname\]" "$main_log" || echo "0")
        changed_count=$(grep -c "changed: \[$hostname\]" "$main_log" || echo "0")
        failed_count=$(grep -c "failed: \[$hostname\]" "$main_log" || echo "0")
        
        echo "Tasks executed: $task_count" >> "$hostname_summary"
        echo "Successful tasks: $ok_count" >> "$hostname_summary"
        echo "Changed tasks: $changed_count" >> "$hostname_summary"
        echo "Failed tasks: $failed_count" >> "$hostname_summary"
        
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
echo "=================================================="

exit $ANSIBLE_EXIT_CODE