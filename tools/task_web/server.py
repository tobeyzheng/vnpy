from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
HOST = '0.0.0.0'
PORT = 8765

TASKS: dict[str, dict[str, Any]] = {
    'hk_sim_task': {
        'name': '港股盘中任务',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_hk_sim_task.py'],
    },
    'hk_sim_close': {
        'name': '港股收盘任务',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_hk_sim_close.py'],
    },
    'us_sim_task': {
        'name': '美股盘中任务',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_us_sim_task.py'],
    },
    'us_sim_close': {
        'name': '美股收盘任务',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_us_sim_close.py'],
    },
    'knot_intraday': {
        'name': 'Knot 盘中决策',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_intraday_knot_decision.py'],
    },
    'knot_refresh_hk': {
        'name': 'Knot 港股刷新',
        'cmd': ['bash', '-lc', 'cd /root/.openclaw/workspace/projects/vnpy && PYTHONPATH=. /usr/bin/python3 scripts/run_knot_agent_hk_refresh.py'],
    },
}

STATE: dict[str, dict[str, Any]] = {
    key: {
        'status': 'stopped',
        'pid': None,
        'started_at': None,
        'ended_at': None,
        'returncode': None,
        'progress': 'idle',
        'logs': deque(maxlen=500),
        'proc': None,
    }
    for key in TASKS
}

INDEX_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><title>Task Manager</title>
<style>
body{font-family:Arial,sans-serif;margin:24px;background:#0b1020;color:#e5e7eb}
.card{border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:16px;background:#111827}
button{margin-right:8px;padding:6px 12px;border-radius:8px;border:0;cursor:pointer}
.start{background:#16a34a;color:white}.stop{background:#dc2626;color:white}.refresh{background:#2563eb;color:white}
pre{white-space:pre-wrap;background:#020617;padding:12px;border-radius:8px;max-height:260px;overflow:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.small{color:#94a3b8;font-size:12px}
</style></head>
<body>
<h1>本地定时任务管理</h1>
<p class="small">支持查看状态/进度/日志，并可启动或停止任务。</p>
<button class="refresh" onclick="load()">刷新</button>
<div id="app" class="grid"></div>
<script>
async function api(path, method='GET'){
  const res = await fetch(path,{method});
  return await res.json();
}
async function startTask(id){ await api('/api/tasks/'+id+'/start','POST'); await load(); }
async function stopTask(id){ await api('/api/tasks/'+id+'/stop','POST'); await load(); }
function esc(s){ return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function load(){
  const data = await api('/api/tasks');
  const app = document.getElementById('app');
  app.innerHTML = data.tasks.map(t => `
    <div class="card">
      <h3>${esc(t.name)}</h3>
      <div>状态: <b>${esc(t.status)}</b></div>
      <div>进度: ${esc(t.progress)}</div>
      <div>PID: ${esc(t.pid || '')}</div>
      <div>开始: ${esc(t.started_at || '')}</div>
      <div>结束: ${esc(t.ended_at || '')}</div>
      <div>退出码: ${esc(t.returncode ?? '')}</div>
      <div style="margin:10px 0">
        <button class="start" onclick="startTask('${esc(t.id)}')">启动</button>
        <button class="stop" onclick="stopTask('${esc(t.id)}')">停止</button>
      </div>
      <pre>${esc((t.logs || []).join('\n'))}</pre>
    </div>
  `).join('');
}
load(); setInterval(load, 5000);
</script></body></html>'''


def now_iso() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def stream_output(task_id: str, pipe, label: str) -> None:
    state = STATE[task_id]
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            text = line.rstrip('\n')
            state['logs'].append(f'[{label}] {text}')
            state['progress'] = text[:120]
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def watch_process(task_id: str, process: subprocess.Popen) -> None:
    rc = process.wait()
    state = STATE[task_id]
    state['status'] = 'stopped' if rc == 0 else 'failed'
    state['returncode'] = rc
    state['ended_at'] = now_iso()
    state['progress'] = 'finished' if rc == 0 else 'failed'
    state['logs'].append(f'[system] exited returncode={rc}')


def start_task(task_id: str) -> dict[str, Any]:
    task = TASKS[task_id]
    state = STATE[task_id]
    proc = state.get('proc')
    if proc and proc.poll() is None:
        return {'ok': True, 'message': 'already running'}
    process = subprocess.Popen(task['cmd'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    state['proc'] = process
    state['status'] = 'running'
    state['pid'] = process.pid
    state['started_at'] = now_iso()
    state['ended_at'] = None
    state['returncode'] = None
    state['progress'] = 'started'
    state['logs'].append(f'[system] started pid={process.pid}')
    threading.Thread(target=stream_output, args=(task_id, process.stdout, 'stdout'), daemon=True).start()
    threading.Thread(target=stream_output, args=(task_id, process.stderr, 'stderr'), daemon=True).start()
    threading.Thread(target=watch_process, args=(task_id, process), daemon=True).start()
    return {'ok': True, 'message': 'started'}


def stop_task(task_id: str) -> dict[str, Any]:
    state = STATE[task_id]
    proc = state.get('proc')
    if not proc or proc.poll() is not None:
        state['status'] = 'stopped'
        return {'ok': True, 'message': 'not running'}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    state['status'] = 'stopped'
    state['ended_at'] = now_iso()
    state['progress'] = 'stopped by user'
    state['logs'].append('[system] stopped by user')
    return {'ok': True, 'message': 'stopped'}


def task_payload(task_id: str) -> dict[str, Any]:
    s = STATE[task_id]
    return {
        'id': task_id,
        'name': TASKS[task_id]['name'],
        'status': s['status'],
        'pid': s['pid'],
        'started_at': s['started_at'],
        'ended_at': s['ended_at'],
        'returncode': s['returncode'],
        'progress': s['progress'],
        'logs': list(s['logs']),
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str, status: int = 200) -> None:
        body = html.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            return self._html(INDEX_HTML)
        if path == '/api/tasks':
            return self._json({'tasks': [task_payload(k) for k in TASKS]})
        if path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) >= 4:
                task_id = parts[3]
                if task_id in TASKS:
                    return self._json(task_payload(task_id))
            return self._json({'error': 'not found'}, 404)
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith('/api/tasks/') and path.endswith('/start'):
            task_id = path.split('/')[3]
            if task_id in TASKS:
                return self._json(start_task(task_id))
            return self._json({'error': 'not found'}, 404)
        if path.startswith('/api/tasks/') and path.endswith('/stop'):
            task_id = path.split('/')[3]
            if task_id in TASKS:
                return self._json(stop_task(task_id))
            return self._json({'error': 'not found'}, 404)
        return self._json({'error': 'not found'}, 404)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'task web running at http://{HOST}:{PORT}', flush=True)
    server.serve_forever()
