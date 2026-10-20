# MultiProbe Toolkit (mp_addon)

A web toolkit for MultiProbe: a live overview of all channels, reports (alarm history, measurements,
SCTE-35 markers) with CSV/XML/PDF export and templates, across one or several independent MultiProbe servers.

Users sign in with **Toolkit accounts**. The first start opens "Create the administrator" (only in a browser
on the computer running the Toolkit). Administrators manage Toolkit users and the list of MultiProbe
servers; every user connects to those servers with accounts of the servers themselves (passwords are not
stored, connections last for the session). The server in `MP_API_URL` is put into the list on the first run.

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
| `app/auth.py` | Toolkit passwords (scrypt) and the lock-out after failed sign-ins |
| `app/sessions.py` | Server-side sessions (the MP token never reaches the browser) |
| `app/i18n.py` | EN/RU strings |
| `app/templates/`, `app/static/` | Pages, styles, wizard script (no CDN — works offline) |
| `app/fonts/` | DejaVu Sans for PDF (free licence alongside) |
| `app/static/vendor/` | ECharts 5.5.1 (Apache 2.0, licence alongside) — bundled, no CDN |
| `docs/MP_API_NOTES.md` | What we learned about the MultiProbe API |
| `docs/swagger.json` | MultiProbe API spec (v26.6) |

## Stages

1. ✅ Skeleton, sign-in through MultiProbe, wizard: report type → server → objects → measures → period → options
2. ✅ Alarm history report: background generation with progress, summary, table (sort, filter, pages), CSV and XML export
2a. ✅ Problem time per object (timeline, availability table); report templates (save, share, copy, run in one click)
3. ✅ Measurement report: server-side aggregation (auto step or 1 min … 1 day, raw up to 24 h), min/avg/max/std,
   coverage, interactive charts (ECharts, zoom synced across charts), CSV/XML export
4. ✅ PDF export (A4 landscape, bundled DejaVu fonts for Cyrillic, timeline and charts drawn as vectors)
4a. ✅ SCTE-35 marker report: markers, ad breaks per profile, timeline, markers missing on some profiles; CSV/XML/PDF
4b. ✅ MultiProbe Toolkit: overview page (channel states now, most problematic channels, one click to a report)
4c. ✅ Several independent MultiProbe servers: shared server list, per-user sign-in, reports and overview across servers
4d. ✅ Toolkit's own users and roles (administrator / user), first-run setup, account page
5. Windows service + offline installer
