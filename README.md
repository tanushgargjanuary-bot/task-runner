# YouTube Metrics Task Runner

A Python command-line worker that reads channel and video metrics from the YouTube Data API, updates local metric and weighting data, and processes a local task queue.

## What it does

The main entry point runs three stages:

1. Fetch channel and video data from the authenticated YouTube account.
2. Update local weights and state from the collected metrics.
3. Process queued tasks.

The worker can also read CSV exports from `data/import/` and match imported metrics to video records.

## Requirements

- Python 3.10 or later is recommended.
- A Google account with access to a YouTube channel.
- YouTube Data API access and valid OAuth credentials.

Install dependencies:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Authentication

The worker looks for OAuth credentials in this order:

1. `AUTH_TOKEN` environment variable.
2. `YOUTUBE_TOKEN_JSON` environment variable.
3. A local `token.json` file in the project directory.
4. A legacy token file in the adjacent `The AI Method` directory.

Use a valid Google OAuth user token. Do not commit token files or paste credentials into source code.

## Run

```bash
python main.py
```

## Local data

The worker creates and uses files under `data/`, including:

- `state.json` — latest collected channel and video metrics.
- `weights.json` — calculated weights.
- `queue.json` — tasks waiting for processing.
- `history.json` — processing history.
- `import/` — optional CSV metric exports.

These files contain local application state. Back them up before deleting or resetting them.

## Important

This project interacts with a real YouTube account through OAuth. Review queued actions before running the worker. Keep OAuth credentials private.
