from flask import Flask, request, render_template, Response, jsonify, redirect, url_for
import subprocess
import sys
import json
import time
import re
import os
from datetime import datetime
from threading import Thread, Lock

app = Flask(__name__)

# Global variables with thread safety
processes = {}  # {hostname: process}
current_tasks = []
current_recap = {}
host_specific_data = {}
data_lock = Lock()

@app.route("/")
def index():
    print("Index route accessed")
    return render_template("index.html")

@app.route("/validation-report")
def validation_report():
    print("Validation report route accessed")
    return render_template("Upgrade.html")

@app.route("/submit", methods=["GET", "POST"])
def handle_submit():
    print("Submit route accessed!")
    print("Request method: {}".format(request.method))
    
    if request.method == "POST":
        print("Request form data: {}".format(dict(request.form)))
        
        # Get the device configuration data
        device_config_json = request.form.get("deviceConfigData")
        action_selected = request.form.get("selectedAction")
        upgrade_to_version = request.form.get("upgradeToVersion")
        download_latest = request.form.get("downloadLatest", "false")
        
        print("=" * 50)
        print("FORM SUBMISSION DATA:")
        print("=" * 50)
        print("Action: {}".format(action_selected))
        print("Upgrade To Version: {}".format(upgrade_to_version))
        print("Download Latest: {}".format(download_latest))
        print("Device Config JSON: {}".format(device_config_json))
        print("=" * 50)

        # Validate required fields
        if not device_config_json or not action_selected:
            error_msg = "Missing required fields. Please complete the form."
            print("Error: {}".format(error_msg))
            return jsonify({"error": error_msg}), 400

        # Parse device configurations
        try:
            device_configs = json.loads(device_config_json)
        except Exception as e:
            return jsonify({"error": "Invalid device configuration data: {}".format(str(e))}), 400

        if len(device_configs) == 0:
            return jsonify({"error": "No devices selected"}), 400

        # Determine build version based on action
        build_version = "22.1.1"
        if action_selected == "upgrade" and upgrade_to_version:
            build_version = upgrade_to_version

        # For upgrade action, process all devices
        if action_selected and action_selected.lower() == "upgrade":
            print("Upgrade action selected - launching script for {} devices".format(len(device_configs)))
            
            # Clear previous data
            global processes, current_tasks, current_recap, host_specific_data
            with data_lock:
                processes = {}
                current_tasks = []
                current_recap = {}
                host_specific_data = {}
            
            # Process each device in parallel
            for device in device_configs:
                vendor = device.get('vendor')
                model = device.get('model')
                dut_ip = device.get('ip')
                username = device.get('username', 'admin')
                password = device.get('password', 'versa123')
                
                print("Processing device: {} {} at {}".format(vendor, model, dut_ip))
                start_upgrade_process(build_version, dut_ip, vendor, model, download_latest, username, password)
            
            return redirect(url_for("validation_report"))
        else:
            # For other actions, process devices sequentially
            for device in device_configs:
                handle_shell_execution(
                    build_version, 
                    device.get('ip'), 
                    device.get('vendor'), 
                    device.get('model'), 
                    action_selected, 
                    download_latest,
                    device.get('username', 'admin'),
                    device.get('password', 'versa123')
                )
            return jsonify({"status": "completed"}), 200
    
    elif request.method == "GET":
        return handle_sse_stream()

def start_upgrade_process(build_version, dut_ip, vendor, model, download_latest="false", username="admin", password="versa123"):
    def run_upgrade():
        global processes, current_tasks, current_recap, host_specific_data
        
        try:
            # Create hostname from model
            hostname = model.lower().replace(' ', '-')
            
            with data_lock:
                host_specific_data[hostname] = {
                    'tasks': [],
                    'status': 'pending',
                    'recap': {},
                    'vendor': vendor,
                    'model': model,
                    'ip': dut_ip
                }
            
            cmd_args = ["./Upgrade_Testing/foldering.sh", build_version, dut_ip, vendor, model, download_latest, username, password]
            
            print("Starting process for {} with command: {}".format(hostname, ' '.join(cmd_args)))
            
            process = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                cwd="."
            )
            
            with data_lock:
                processes[hostname] = process
            
            print("Process started for {} with PID: {}".format(hostname, process.pid))
            
            for line in iter(process.stdout.readline, ''):
                if line:
                    parse_ansible_output(line.strip(), hostname)
                    
            return_code = process.wait()
            print("Process for {} completed with return code: {}".format(hostname, return_code))
            
            with data_lock:
                if hostname in processes:
                    del processes[hostname]
            
        except Exception as e:
            print("Error starting process for {}: {}".format(hostname, str(e)))
            with data_lock:
                current_tasks.append({
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'name': 'Process Error',
                    'host': hostname,
                    'status': 'failed',
                    'details': str(e)
                })
                if hostname in host_specific_data:
                    host_specific_data[hostname]['status'] = 'failed'
    
    thread = Thread(target=run_upgrade)
    thread.daemon = True
    thread.start()

def parse_ansible_output(line, hostname=None):
    global current_tasks, current_recap, host_specific_data
    
    print("Parsing line for {}: {}".format(hostname, line))
    
    with data_lock:
        if line.startswith("TASK ["):
            task_match = re.match(r"TASK \[([^\]]+)\]", line)
            if task_match:
                task_name = task_match.group(1)
                current_task = {
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'name': task_name,
                    'host': hostname if hostname else 'pending',
                    'status': 'running',
                    'details': 'In progress...'
                }
                current_tasks.append(current_task)
                
                if hostname and hostname in host_specific_data:
                    host_specific_data[hostname]['tasks'].append(current_task.copy())
                    host_specific_data[hostname]['status'] = 'running'
        
        elif any(line.startswith(prefix) for prefix in ["ok:", "changed:", "failed:", "unreachable:", "skipped:", "fatal:"]):
            result_match = re.match(r"(ok|changed|failed|unreachable|skipped|fatal): \[([^\]]+)\](?:\s*=>\s*(.*))?", line)
            if result_match:
                status = result_match.group(1)
                host = result_match.group(2)
                details = result_match.group(3) or ""
                
                # Extract hostname if it contains arrow notation
                if '->' in host:
                    host = host.split('->')[0].strip()
                
                # Mark fatal as failed
                if status == 'fatal':
                    status = 'failed'
                
                if host in host_specific_data and host_specific_data[host]['tasks']:
                    last_task = host_specific_data[host]['tasks'][-1]
                    last_task['host'] = host
                    last_task['status'] = status
                    last_task['details'] = details[:200] + "..." if len(details) > 200 else details
                    
                    if status in ['failed', 'unreachable']:
                        host_specific_data[host]['status'] = 'failed'
                    elif status in ['ok', 'changed'] and host_specific_data[host]['status'] != 'failed':
                        host_specific_data[host]['status'] = 'running'
                
                if current_tasks:
                    current_tasks[-1]['host'] = host
                    current_tasks[-1]['status'] = status
                    current_tasks[-1]['details'] = details[:100] + "..." if len(details) > 100 else details
        
        # Detect rescue block failures
        elif "FAILED!" in line and "=>" in line:
            # This catches lines like: fatal: [csg2500]: FAILED! => {"changed": true, ...}
            failed_match = re.search(r'\[([^\]]+)\].*FAILED!', line)
            if failed_match:
                host = failed_match.group(1)
                if '->' in host:
                    host = host.split('->')[0].strip()
                
                if host in host_specific_data and host_specific_data[host]['tasks']:
                    last_task = host_specific_data[host]['tasks'][-1]
                    last_task['status'] = 'failed'
                    
                    # Extract error message from JSON if available
                    try:
                        json_match = re.search(r'=>\s*({.*})', line)
                        if json_match:
                            error_data = json.loads(json_match.group(1))
                            error_msg = error_data.get('msg', error_data.get('stderr', 'Task failed'))
                            last_task['details'] = error_msg[:200]
                    except:
                        last_task['details'] = 'Task failed - see logs for details'
                    
                    host_specific_data[host]['status'] = 'failed'
        
        # Detect rescue blocks being triggered
        elif line.strip().startswith("TASK [Set") and "failure" in line.lower():
            # Tasks like "Set upgrade completion status after failure" indicate a rescue
            if hostname and hostname in host_specific_data:
                # Mark the previous task as failed if it's still running
                if host_specific_data[hostname]['tasks']:
                    for i in range(len(host_specific_data[hostname]['tasks']) - 1, -1, -1):
                        task = host_specific_data[hostname]['tasks'][i]
                        if task['status'] == 'running':
                            task['status'] = 'failed'
                            task['details'] = 'Task failed - triggered rescue block'
                            break
        
        elif "PLAY RECAP" in line:
            pass
        elif re.match(r"^[a-zA-Z0-9.-]+\s*:\s*ok=\d+", line):
            recap_match = re.match(r"^([a-zA-Z0-9.-]+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)(?:\s+skipped=(\d+))?\s+(?:rescued=(\d+))?", line)
            if recap_match:
                hostname_recap = recap_match.group(1)
                failed_count = int(recap_match.group(5))
                rescued_count = int(recap_match.group(7)) if recap_match.group(7) else 0
                
                # If there are rescued tasks, count them as failures
                total_failures = failed_count + rescued_count
                
                recap_data = {
                    'host': hostname_recap,
                    'ok': int(recap_match.group(2)),
                    'changed': int(recap_match.group(3)),
                    'unreachable': int(recap_match.group(4)),
                    'failed': total_failures,
                    'rescued': rescued_count
                }
                current_recap[hostname_recap] = recap_data
                
                if hostname_recap in host_specific_data:
                    host_specific_data[hostname_recap]['recap'] = recap_data
                    
                    if total_failures > 0:
                        host_specific_data[hostname_recap]['status'] = 'failed'
                    elif recap_data['unreachable'] > 0:
                        host_specific_data[hostname_recap]['status'] = 'unreachable'
                    else:
                        host_specific_data[hostname_recap]['status'] = 'completed'

def handle_shell_execution(build_version, dut_ip, vendor, model, action_selected, download_latest="false", username="admin", password="versa123"):
    print("Running shell script with build version: {}".format(build_version))
    print("DUT IP: {}".format(dut_ip))
    
    cmd = ["./Upgrade_Testing/foldering.sh", build_version, dut_ip, vendor, model, download_latest, username, password]
            
    print("Running command: {}".format(' '.join(cmd)))
    
    def generate():
        try:
            yield "Starting shell script execution...\n"
            yield "Command: {}\n".format(' '.join(cmd))
            yield "Build Version: {}\n".format(build_version)
            yield "Vendor: {}\n".format(vendor)
            yield "Model: {}\n".format(model)
            yield "DUT IP: {}\n".format(dut_ip)
            yield "Action: {}\n".format(action_selected)
            yield "=" * 60 + "\n"
            
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                bufsize=1, 
                universal_newlines=True,
                cwd="."
            )
            
            for line in iter(process.stdout.readline, ''):
                if line:
                    yield line
                    sys.stdout.flush()
            
            process.stdout.close()
            return_code = process.wait()
            
            if return_code == 0:
                yield "\n" + "=" * 60 + "\n"
                yield "Shell script completed successfully!\n"
                yield "Return code: {}\n".format(return_code)
            else:
                yield "\n" + "=" * 60 + "\n"
                yield "Shell script completed with errors!\n"
                yield "Return code: {}\n".format(return_code)
                
        except Exception as e:
            yield "Error executing shell script: {}\n".format(str(e))
            print("Error in generate(): {}".format(str(e)))
    
    return Response(
        generate(), 
        mimetype='text/plain',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )

def handle_sse_stream():
    print("SSE stream requested")

    def generate():
        try:
            global processes, current_tasks, current_recap, host_specific_data
            
            yield "data: {}\n\n".format(json.dumps({
                'type': 'log', 
                'message': 'Connected to upgrade process stream...'
            }))
            
            with data_lock:
                if len(processes) == 0:
                    print("No processes running, sending error")
                    yield "data: {}\n\n".format(json.dumps({
                        'type': 'error', 
                        'message': 'No upgrade process is currently running. Please start an upgrade from the main form first.'
                    }))
                    return

            print("Processes found, streaming output...")
            
            last_task_count = 0
            last_recap_update = {}
            last_host_data_update = {}
            
            while True:
                try:
                    with data_lock:
                        active_processes = len(processes)
                    
                    if active_processes == 0:
                        # All processes completed
                        print("All processes completed")
                        
                        with data_lock:
                            final_tasks = []
                            for hostname, data in host_specific_data.items():
                                final_tasks.extend(data['tasks'])
                            
                            yield "data: {}\n\n".format(json.dumps({
                                'type': 'tasks',
                                'data': {
                                    'tasks': final_tasks,
                                    'recap': current_recap,
                                    'host_data': host_specific_data
                                }
                            }))
                            
                            yield "data: {}\n\n".format(json.dumps({
                                'type': 'complete', 
                                'return_code': 0
                            }))
                        break
                    
                    with data_lock:
                        data_changed = (
                            len(current_tasks) != last_task_count or 
                            current_recap != last_recap_update or
                            host_specific_data != last_host_data_update
                        )
                    
                    if data_changed:
                        with data_lock:
                            all_tasks = []
                            for hostname, data in host_specific_data.items():
                                all_tasks.extend(data['tasks'])
                            
                            yield "data: {}\n\n".format(json.dumps({
                                'type': 'tasks',
                                'data': {
                                    'tasks': all_tasks,
                                    'recap': current_recap,
                                    'host_data': host_specific_data
                                }
                            }))
                            
                            last_task_count = len(current_tasks)
                            last_recap_update = current_recap.copy() if current_recap else {}
                            last_host_data_update = {k: v.copy() for k, v in host_specific_data.items()}
                    
                    time.sleep(1)
                    
                except Exception as e:
                    print("Error in SSE loop: {}".format(str(e)))
                    yield "data: {}\n\n".format(json.dumps({
                        'type': 'error', 
                        'message': 'Error reading process output: {}'.format(str(e))
                    }))
                    break

        except GeneratorExit:
            print("SSE client disconnected")
        except Exception as e:
            print("Error in SSE stream: {}".format(str(e)))
            yield "data: {}\n\n".format(json.dumps({
                'type': 'error', 
                'message': str(e)
            }))

    return Response(generate(), mimetype='text/event-stream', headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    })

@app.route("/api/shell-status")
def shell_status():
    global processes, host_specific_data
    
    with data_lock:
        active_count = len(processes)
        if active_count == 0:
            status = "no_process"
            message = "No upgrade process running"
        else:
            status = "running"
            message = "{} upgrade process(es) running".format(active_count)
    
    return jsonify({
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "active_processes": active_count,
        "tasks": current_tasks,
        "recap": current_recap,
        "host_data": host_specific_data
    })

if __name__ == "__main__":
    app.run(host="10.70.188.51", port=5000, debug=True, threaded=True)