# MultiProbe API — notes for the report add-on

Verified live against MultiProbe API **v26.6.0.4464 (.NET 8)** on 2026-10-20.
Full OpenAPI spec: `docs/swagger.json` (UI: `http://<host>:40000/swagger/index.html`,
spec: `/swagger/v2/swagger.json`).

## Authentication

- `POST /api/Admin/Login/CreateLogin` with `{"login": "...", "password": "..."}` (no auth needed).
- On success (200) the token comes back as the **`access_token` cookie**; the JSON body holds
  `profile` (`userID`, `loginName`, `userName`, `userRoleIDs`, `timeZoneSettings.timeZone`, ...).
- Every following request must send that cookie. `GET /api/Admin/Login/GetProfile` checks it.
- Errors (400) return `{"error": "<WebError enum>", "message": "...", "badLoginCount", "availableLoginCount", "bannedTime"}`.
  Relevant codes: `authErrorWrongLoginPassword`, `authErrorUserDisabled`, `authErrorUserBanned`, `authErrorUnauthorized`.
- UI language is **not** stored server-side (`/api/Admin/UserOption/Get` has no language key;
  `/api/Frontend/Language/Get` lists installed UI languages, only `en` here). The add-on uses its own
  EN/RU switch, defaulting to the browser language.

## Dictionaries

| What | Endpoint | Notes |
|---|---|---|
| Nodes ("servers" in the wizard) | `POST /api/Probe/Node/Find` `{}` | `nodeID`, `name`, `nodeType` (`system` = DB server, `probe` = real probes) |
| Monitoring objects | `POST /api/Probe/MonitoringObject/Find` `{"nodeID": n}` | `monitoringObjectID`, `name`, `objectKind`, `parentID`, `analyzerItemList[]`, `hasMeasures` |
| Analyzers + measures | `POST /api/Monitor/AnalyzerMeasure/Find` `{}` | per analyzer `measureItemList[]`: `name`, **`columnName`**, `unit`, `dataType` |
| Alarm definitions | `POST /api/Monitor/Alarm/Find` | holds the fire condition text (not present in the log) |

## Alarm history

`POST /api/Alarm/MonitoringObjectLog/Find`

```json
{"dataFilter": {"beginDate": "...Z", "endDate": "...Z",
                "pagingProperty": {"topCount": 100000, "fromIndex": 0}},
 "priority": 370, "alarmName": "...", "monitoringObjectName": "...", "nodeName": "..."}
```

- **Default page size is 20** — always pass `pagingProperty`.
- Times come back in the user's time zone (`+03:00`).
- Active alarms: `isActive=true`, no `alarmEnd`.
- **Duplicates:** the same event is returned once per compound (view) object it belongs to —
  the stream, the service and the object itself: same `objectAnalyzerLogID` + `monitoringObjectID`,
  different `compoundObjectID`. Deduplicate on (`objectAnalyzerLogID`, `monitoringObjectID`, `alarmID`, `alarmBegin`).
- **Object filter:** `"analyzeMonitoringObjectList": [{"compoundObjectID": "<id>"}]` (analyzerID optional)
  returns rows whose *compound* is that object — for a stream that is every event inside it.
  Verified: fetching the 2 streams gives exactly the same 729 events as fetching all 55 objects one by one.
  So the add-on requests only the top-most selected objects and filters by `monitoringObjectID` locally.
- **Period** = overlap: events that started before the end and were still active after the start.
- **Speed is superlinear in page size:** 200 rows ~2.7 s, 500 ~5 s, 1000 ~15 s, 2700 rows in one page ~100 s,
  30 days unfiltered timed out. The add-on pages by 500 and splits the period into 6 h windows, 4 in parallel.
- Without an object filter `itemCount` just echoes the page size — it is not the total.

## Measurements

`POST /api/Measurement/LogData/Find`

```json
{"dataFilter": {"beginDate": "...Z", "endDate": "...Z",
                "pagingProperty": {"topCount": 100000, "fromIndex": 0},
                "interpolation": {"forceDisableInterpolation": true}},
 "analyzeMonitoringObjectList": [{"analyzerID": 45, "compoundObjectID": "<monitoringObjectID>"}],
 "analyzerMeasuresList": [{"analyzerID": 45, "measureNameList": ["SilenceLevel", "OverloadLevel"]}]}
```

- `measureNameList` takes the measure **`columnName`**, not the display name.
- Default page size is 20 here too.
- `forceDisableInterpolation: true` → raw points in `dataList[]` (`measureTime` UTC, `valuesMap`), newest first.
- Without it the server aggregates into `boxPlotDataList[]` (5-min buckets for a 2 h period:
  min / lower quartile / median / average / upper quartile / max) — good for long periods.
- `POST /api/Measurement/LogData/FindAverages` (same body) → average + standard deviation per measure.
- `interpolationIntervalType` enum: `seconds`, `minutes`, `hours`, `days`. With
  `{"interpolationIntervalType": "minutes", "interpolationInterval": 5}` the buckets are exactly 5 min
  (empty buckets come back with `{}` values). 24 h at 1 min = ~1440 buckets per object, answered in well under a second.
- `availableBoxPlotCount: N` makes the server choose the step itself; `availablePointCount` has no effect.
- Several objects per request are fine (each gets its own `measurementResponseItemList` entry), also for raw data.
- `FindAverages` returns `data[]` **without an object id** — only the order tells which object it is,
  so the add-on calls it once per object.
