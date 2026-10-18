import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request


def stop(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main():
    original = '/opt/env-runner/environment-manager'
    if len(sys.argv) < 2 or sys.argv[1] != 'task-run':
        os.execv(original, [original, *sys.argv[1:]])
    servers = []
    manager = None

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        services = [(Path(__file__).with_name('server.py'), 8765)]
        workbench = Path('/opt/oma-tools/server.py')
        if workbench.is_file():
            services.append((workbench, 8766))
        for script, port in services:
            server = subprocess.Popen([sys.executable, str(script)], start_new_session=True, stdin=subprocess.DEVNULL)
            servers.append(server)
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f'Office tool service on port {port} exited during startup')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Office tool service on port {port} startup timed out')
                    time.sleep(0.05)
        manager = subprocess.Popen([original, *sys.argv[1:]], start_new_session=True)
        while manager.poll() is None:
            if any(server.poll() is not None for server in servers):
                raise RuntimeError('Office tool service stopped while the Worker was active')
            time.sleep(0.2)
        return manager.returncode
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop(manager)
        for server in reversed(servers):
            stop(server)


if __name__ == '__main__':
    sys.exit(main())
