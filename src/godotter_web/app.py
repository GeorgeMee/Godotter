from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse


app = FastAPI(title='Godotter Web Console', version='0.0.1')

ENV_FILENAME = '.env'


def _repo_root() -> Path:
    # bin/run_web.* sets CWD to repo root; this is a fallback.
    cwd = Path.cwd()
    if (cwd / 'pyproject.toml').exists():
        return cwd
    for parent in [cwd, *cwd.parents]:
        if (parent / 'pyproject.toml').exists():
            return parent
    return cwd


def _env_path() -> Path:
    return _repo_root() / ENV_FILENAME


def _require_token_if_configured(request: Request) -> None:
    token = (os.getenv('GODOTTER_WEB_TOKEN') or '').strip()
    if not token:
        return
    header = (request.headers.get('x-godotter-token') or '').strip()
    if header == token:
        return
    raise HTTPException(status_code=401, detail='missing_or_invalid_token')


def _write_env_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    path.write_bytes(normalized.encode('utf-8'))


@app.get('/health')
def health() -> dict[str, object]:
    return {'ok': True}


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    # Minimal placeholder UI; the real web console will replace this.
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter Web Console</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
      code { background: #f4f4f5; padding: 2px 6px; border-radius: 6px; }
    </style>
  </head>
  <body>
    <h1>Godotter Web Console</h1>
    <p>Backend is running.</p>
    <p>Health check: <code>/health</code></p>
  </body>
</html>
"""


@app.get('/env', response_class=HTMLResponse)
def env_editor(request: Request) -> str:
    _require_token_if_configured(request)
    path = _env_path()
    content = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    token_enabled = bool((os.getenv('GODOTTER_WEB_TOKEN') or '').strip())
    hint = 'Token required (x-godotter-token).' if token_enabled else 'No token configured.'
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter .env Editor</title>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }}
      textarea {{ width: 100%; height: 60vh; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
      .row {{ display:flex; gap: 12px; align-items:center; flex-wrap: wrap; }}
      .muted {{ color: #666; }}
      button {{ padding: 10px 14px; }}
      code {{ background: #f4f4f5; padding: 2px 6px; border-radius: 6px; }}
    </style>
  </head>
  <body>
    <h1>.env Editor</h1>
    <p class="muted">Path: <code>{path.as_posix()}</code> — {hint}</p>
    <div class="row">
      <button id="save">Save</button>
      <span id="status" class="muted"></span>
    </div>
    <p></p>
    <textarea id="text" spellcheck="false">{content}</textarea>
    <script>
      const btn = document.getElementById('save');
      const status = document.getElementById('status');
      btn.addEventListener('click', async () => {{
        status.textContent = 'Saving...';
        const resp = await fetch('/api/env', {{
          method: 'PUT',
          headers: {{ 'content-type': 'text/plain' }},
          body: document.getElementById('text').value
        }});
        if (resp.ok) {{
          status.textContent = 'Saved.';
        }} else {{
          const t = await resp.text();
          status.textContent = 'Error: ' + t;
        }}
      }});
    </script>
  </body>
</html>
"""


@app.get('/api/env', response_class=PlainTextResponse)
def env_get(request: Request) -> str:
    _require_token_if_configured(request)
    path = _env_path()
    return path.read_text(encoding='utf-8-sig') if path.exists() else ''


@app.put('/api/env', response_class=PlainTextResponse)
async def env_put(request: Request) -> str:
    _require_token_if_configured(request)
    payload = (await request.body()).decode('utf-8', errors='replace')
    _write_env_text(_env_path(), payload)
    return 'ok'
