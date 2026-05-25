# Divoom Studio redesign

This branch turns the old inline debug dashboard into a local Studio UI for designing and previewing Pixoo 64 layouts.

## What changed

- Moved the web UI out of the 2,500-line Python string and into `src/divoom_client/web/static/index.html`.
- Added a modern responsive Studio interface:
  - layout sidebar with search and active badges
  - integrated design canvas and server-rendered preview toggle
  - layer list and property editor
  - readable data-health cards plus raw JSON for debugging
  - data-source editor for stocks, weather, and generic HTTP sources
  - source test/toggle/save/delete controls with inline errors
  - activity log and design clipping warnings
  - disabled send button when the device is disconnected
- Added complete layout persistence operations:
  - save current layout
  - save as
  - duplicate
  - rename
  - delete
  - import/export JSON
- Added safe backend filename validation so layout operations stay inside `config/layouts`.
- Added metadata endpoint: `GET /api/layouts/meta`.
- Hardened rendering so missing numeric data no longer crashes formatted text widgets.

## How to run locally

```bash
cd /Users/jsperson/source/divoom_client
. .venv/bin/activate
divoom serve config/layouts/Daily2.json --web --port 8091 --no-device
```

Open `http://127.0.0.1:8091`.

## Verification performed

Local Mac:

```bash
pytest -q
ruff check src/divoom_client/web/app.py src/divoom_client/core/renderer.py tests/test_web_layouts.py tests/test_renderer.py
python -m pip wheel . -w /tmp/divoom-wheel-test
```

Results:

- `5 passed`
- targeted ruff checks passed
- wheel build included `divoom_client/web/static/index.html`
- browser QA loaded the Studio page, verified responsive layout, rendered preview mode, data tab, and save/duplicate/rename/delete API round trip

Rack Pi safe test path:

- Synced this branch to `/tmp/divoom_client_studio_test` only.
- Installed/tested in that temporary clone.
- Did not modify `/home/jsperson/divoom_client` code or the running `divoom@jsperson.service`.
- Verified the alternate temporary web app on port `8092`.
- Tested device connectivity and sent `config/layouts/demo.json` to screen from the temp clone. Production service stayed active and resumed its normal 60-second `Daily2` refresh.

## Deployment notes

Production service currently runs from:

```text
/home/jsperson/divoom_client
systemd service: divoom@jsperson.service
command: divoom_client.cli serve config/layouts/Daily2.json --web --port 8080
```

To deploy later, merge/copy this branch into `/home/jsperson/divoom_client`, install the package, then restart `divoom@jsperson.service`. Do not do that until intentionally promoting this branch.
