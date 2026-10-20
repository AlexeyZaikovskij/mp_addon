# MultiProbe Reports (mp_addon)

A web page for building alarm-history and measurement reports from MultiProbe.
Users sign in with their MultiProbe account; the data comes from the MultiProbe REST API.

## Run (development)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy config.example.env config.env
.venv\Scripts\python run.py
```

Open http://localhost:8090 and sign in with a MultiProbe user.

While developing, use `.venv\Scripts\python run.py --dev`: the server restarts by itself
when a file under `app/` changes, and listens on localhost only.

## Layout

| Path | What |
|---|---|
| `app/main.py` | Routes: login/logout, wizard page, wizard data API |
| `app/mp_client.py` | MultiProbe API client |
| `app/storage.py` | Report templates (SQLite in `data/addon.db`, set `DATA_DIR` to move it) |
| `app/reports/` | Alarm report: fetching, availability, timeline, CSV/XML export |
| `app/sessions.py` | Server-side sessions (the MP token never reaches the browser) |
| `app/i18n.py` | EN/RU strings |
| `app/templates/`, `app/static/` | Pages, styles, wizard script (no CDN — works offline) |
| `docs/MP_API_NOTES.md` | What we learned about the MultiProbe API |
| `docs/swagger.json` | MultiProbe API spec (v26.6) |

## Stages

1. ✅ Skeleton, sign-in through MultiProbe, wizard: report type → server → objects → measures → period → options
2. ✅ Alarm history report: background generation with progress, summary, table (sort, filter, pages), CSV and XML export
2a. ✅ Problem time per object (timeline, availability table); report templates (save, share, copy, run in one click)
3. Measurement report: aggregation, chart
4. PDF export
5. Windows service + offline installer
