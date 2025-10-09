from flask import Flask, request, render_template, Response, jsonify, redirect, url_for
import subprocess
import sys
import json
import time
import re
import os
from datetime import datetime
from threading import Thread

app = Flask(__name__)

process = None
current_tasks = []
current_recap = {}
host_specific_data = {}

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
        
        # Get form fields
        vendor = request.form.get("vendor")
        model = request.form.get("model")
        dut_ip = request.form.get("finalDutIP")
        action_selected = request.form.get("selectedAction")
        upgrade_to_version = request.form.get("upgradeToVersion")
        downgrade_from_version = request.form.get("downgradeFromVersion")
        download_latest = request.form.get("downloadLatest", "false")
        
        print("=" * 50)
        print("FORM SUBMISSION DATA:")
        print("=" * 50)
        print("Vendor: {}".format(vendor))
        print("Model: {}".format(model))
        print("DUT IP: {}".format(dut_ip))
        print("Action: {}".format(action_selected))
        print("Upgrade To Version: {}".format(upgrade_to_version))
        print("Downgrade From Version: {}".format(downgrade_from_version))
        print("Download Latest: {}".format(download_latest))
        print("=" * 50)

        # Validate required fields
        if not all([vendor, model, dut_ip, action_selected]):
            error_msg = "Missing required fields. Please complete the form."
            print("Error: {}".format(error_msg))
            return jsonify({"error": error_msg}), 400

        # Determine build version based on action
        build_version = "22.1.1"
        if action_selected == "upgrade" and upgrade_to_version:
            build_version = upgrade_to_version
        elif action_selected == "downgrade" and downgrade_from_version:
            build_version = downgrade_from_version

        # For upgrade action, start process and redirect to validation report
        if action_selected and action_selected.lower() == "upgrade":
            print("Upgrade action selected - launching script in background")
            start_upgrade_process(build_version, dut_ip, vendor, model, download_latest)
            return redirect(url_for("validation_report"))
        else:
            return handle_shell_execution(build_version, dut_ip, vendor, model, action_selected, download_latest)
    
    elif request.method == "GET":
        return handle_sse_stream()

def start_upgrade_process(build_version, dut_ip, vendor, model, download_latest="false"):
    def run_upgrade():
        global process, current_tasks, current_recap, host_specific_data
        
        try:
            current_tasks = []
            current_recap = {}
            host_specific_data = {}
            
            # Create hostname from model
            hostname = model.lower().replace(' ', '-')
            
            host_specific_data[hostname] = {
                'tasks': [],
                'status': 'pending',
                'recap': {}
            }
            
            cmd_args = ["./Upgrade_Testing/foldering.sh", build_version, dut_ip, vendor, model, download_latest]
            
            print("Starting process with command: {}".format(' '.join(cmd_args)))
            
            process = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                cwd="."
            )
            print("Process started with PID: {}".format(process.pid))
            
            for line in iter(process.stdout.readline, ''):
                if line:
                    parse_ansible_output(line.strip())
                    
            return_code = process.wait()
            print("Process completed with return code: {}".format(return_code))
            
        except Exception as e:
            print("Error starting process: {}".format(str(e)))
            current_tasks.append({
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'name': 'Process Error',
                'host': 'localhost',
                'status': 'failed',
                'details': str(e)
            })
    
    thread = Thread(target=run_upgrade)
    thread.daemon = True
    thread.start()

def parse_ansible_output(line):
    global current_tasks, current_recap, host_specific_data
    
    print("Parsing line: {}".format(line))
    
    if line.startswith("TASK ["):
        task_match = re.match(r"TASK \[([^\]]+)\]", line)
        if task_match:
            task_name = task_match.group(1)
            current_task = {
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'name': task_name,
                'host': 'pending',
                'status': 'running',
                'details': 'In progress...'
            }
            current_tasks.append(current_task)
            
            for hostname in host_specific_data.keys():
                host_specific_data[hostname]['tasks'].append(current_task.copy())
                host_specific_data[hostname]['status'] = 'running'
    
    elif any(line.startswith(prefix) for prefix in ["ok:", "changed:", "failed:", "unreachable:", "skipped:"]):
        result_match = re.match(r"(ok|changed|failed|unreachable|skipped): \[([^\]]+)\](?:\s*=>\s*(.*))?", line)
        if result_match:
            status = result_match.group(1)
            host = result_match.group(2)
            details = result_match.group(3) or ""
            
            if host in host_specific_data and host_specific_data[host]['tasks']:
                last_task = host_specific_data[host]['tasks'][-1]
                last_task['host'] = host
                last_task['status'] = status
                last_task['details'] = details[:200] + "..." if len(details) > 200 else details
                
                if status == 'failed':
                    host_specific_data[host]['status'] = 'failed'
                elif status in ['ok', 'changed'] and host_specific_data[host]['status'] != 'failed':
                    host_specific_data[host]['status'] = 'running'
            
            if current_tasks:
                current_tasks[-1]['host'] = host
                current_tasks[-1]['status'] = status
                current_tasks[-1]['details'] = details[:100] + "..." if len(details) > 100 else details
    
    elif "PLAY RECAP" in line:
        pass
    elif re.match(r"^[a-zA-Z0-9.-]+\s*:\s*ok=\d+", line):
        recap_match = re.match(r"^([a-zA-Z0-9.-]+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", line)
        if recap_match:
            hostname = recap_match.group(1)
            recap_data = {
                'host': hostname,
                'ok': int(recap_match.group(2)),
                'changed': int(recap_match.group(3)),
                'unreachable': int(recap_match.group(4)),
                'failed': int(recap_match.group(5))
            }
            current_recap = recap_data
            
            if hostname in host_specific_data:
                host_specific_data[hostname]['recap'] = recap_data
                
                if recap_data['failed'] > 0:
                    host_specific_data[hostname]['status'] = 'failed'
                elif recap_data['unreachable'] > 0:
                    host_specific_data[hostname]['status'] = 'unreachable'
                else:
                    host_specific_data[hostname]['status'] = 'completed'

def handle_shell_execution(build_version, dut_ip, vendor, model, action_selected, download_latest="false"):
    print("Running shell script with build version: {}".format(build_version))
    print("DUT IP: {}".format(dut_ip))
    
    cmd = ["./Upgrade_Testing/foldering.sh", build_version, dut_ip, vendor, model, download_latest]
            
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
            global process, current_tasks, current_recap, host_specific_data
            
            yield "data: {}\n\n".format(json.dumps({
                'type': 'log', 
                'message': 'Connected to upgrade process stream...'
            }))
            
            if process is None:
                print("No process running, sending error")
                yield "data: {}\n\n".format(json.dumps({
                    'type': 'error', 
                    'message': 'No upgrade process is currently running. Please start an upgrade from the main form first.'
                }))
                return

            print("Process found, streaming output...")
            
            last_task_count = 0
            last_recap_update = {}
            last_host_data_update = {}
            
            while True:
                try:
                    if process.poll() is not None:
                        return_code = process.returncode
                        print("Process completed with return code: {}".format(return_code))
                        
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
                            'return_code': return_code
                        }))
                        break
                    
                    data_changed = (
                        len(current_tasks) != last_task_count or 
                        current_recap != last_recap_update or
                        host_specific_data != last_host_data_update
                    )
                    
                    if data_changed:
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
                        last_host_data_update = host_specific_data.copy()
                    
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
    global process, host_specific_data
    
    if process is None:
        status = "no_process"
        message = "No upgrade process running"
    elif process.poll() is None:
        status = "running"
        message = "Upgrade process is running (PID: {})".format(process.pid)
    else:
        status = "completed"
        message = "Process completed with return code: {}".format(process.returncode)
        process = None
    
    return jsonify({
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "tasks": current_tasks,
        "recap": current_recap,
        "host_data": host_specific_data
    })

if __name__ == "__main__":
    app.run(host="10.70.188.51", port=5000, debug=True, threaded=True)