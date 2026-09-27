"""Own the replay subprocess and expose its acknowledged state to the UI."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
import device
import routes

ROOT = Path(__file__).resolve().parent


class Replay:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.connected = threading.Event()
        self.info = {'state': 'idle', 'active': False, 'cleared': False}

    def status(self):
        with self.lock:
            return self.info.copy()

    def start(self, ready, plan, run_id):
        with self.lock:
            if self.info.get('active'):
                raise device.DeviceError('已有路线正在回放，请先停止。')
            plan_file = ROOT / 'runtime' / f'plan-{run_id}.json'
            plan_file.write_text(json.dumps(plan))
            command = [sys.executable, str(ROOT/'replay_worker.py'),
                       *device.connection_arguments(ready['_serial']), str(plan_file)]
            try:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                                cwd=ROOT, env=device._environment())
            except Exception:
                plan_file.unlink(missing_ok=True)
                raise
            self.info = {'state': 'starting', 'active': True, 'cleared': False,
                         'run_id': run_id, 'duration': plan['duration'], 'metres': plan['metres'],
                         'mode': plan.get('mode', 'route'),
                         'parameters': {k: plan[k] for k in ('distance_km', 'pace', 'variation') if k in plan},
                         'device_version': ready.get('version', ''),
                         'demo': plan['demo'], **routes.frame(plan, 0)}
            self.connected = threading.Event()
            threading.Thread(target=self._read, args=(self.process, plan_file), daemon=True).start()
            threading.Thread(target=self._deadline, args=(self.process, self.connected), daemon=True).start()
            return self.status()

    def keepalive(self, run_id):
        with self.lock:
            if not self.info.get('active') or self.info.get('run_id') != run_id:
                raise device.DeviceError('固定定位会话已结束。')
            lease = ROOT / 'runtime' / f'lease-{run_id}'
            if not lease.exists():
                raise device.DeviceError('该会话不接受远程续期。')
            temporary = lease.with_suffix('.tmp')
            temporary.write_text(str(time.monotonic()))
            temporary.replace(lease)

    def _deadline(self, process, connected):
        if not connected.wait(50) and process.poll() is None:
            device._finish_process(process)

    def _read(self, process, plan_file):
        try:
            for line in process.stdout:
                if not line.startswith('TRACKLAB_EVENT '):
                    continue
                try:
                    event = json.loads(line[len('TRACKLAB_EVENT '):])
                    with self.lock:
                        self.info.update(event)
                        if event.get('state') == 'running':
                            self.connected.set()
                except (ValueError, TypeError):
                    continue
            code = process.wait()
            with self.lock:
                if code or self.info['state'] not in {'finished', 'stopped'} or not self.info['cleared']:
                    self.info['state'] = 'failed'
        finally:
            plan_file.unlink(missing_ok=True)
            (plan_file.parent / plan_file.stem.replace('plan-', 'lease-')).unlink(missing_ok=True)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            # Publish inactive only after all writes from this worker are over.
            with self.lock:
                self.connected.set()
                if self.info['state'] not in {'finished', 'stopped', 'failed'}:
                    self.info['state'] = 'failed'
                self.info['active'] = False

    def control(self, command):
        with self.lock:
            if not self.info.get('active') or self.process is None or self.process.poll() is not None:
                raise device.DeviceError('当前没有正在回放的路线。')
            try:
                self.process.stdin.write(command+'\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise device.DeviceError('回放连接已断开，请使用恢复按钮清除模拟。') from None
            if command == 'stop':
                self.info['state'] = 'stopping'
            return self.status()

    def stop_and_wait(self):
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return
        self.control('stop')
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            device._finish_process(process)


runner = Replay()
