from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


app = FastAPI(title='Godotter Web Console', version='0.0.1')


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

