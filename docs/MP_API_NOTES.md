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

**Retention:** measurements are kept only a few days by default — MultiProbe's Administration → Cleanup,
"Days to expire" **3** for every `Measure.*_Cleanup` table (431 tables, "Bulk change" edits several). The alarm
log goes back months (Hetzner: events 90 days ago), so the Patterns page works on alarms; a longer measurement
history needs a bigger "Days to expire" there (or the Toolkit collecting its own aggregates).

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

## SCTE-35 markers

- Enabled per stream with a probe preset that has the SCTE-35 decoder and "record SCTE messages to database"
  (`MpegDecoderPreset.scte35.recordScteMessagesToDatabase`). For HLS MultiProbe then creates a virtual decoder
  per profile, **"<profile>; PID: 8191; Scte35"**, fed from the playlist tags (`#EXT-X-DATERANGE` / `CUE-OUT`).
- That decoder carries analyzer **51 "SCTE-35 Analyzer"** (and 286 "SCTE35 Decoder Measurement Verification",
  `ElapsedTime` = seconds since the last marker).
- Read the markers with `LogData/Find`, analyzer 51, `forceDisableInterpolation: true`. One raw row per message:
  `splice_command_type` (5 = splice_insert, 6 = time_signal, 0 = splice_null …) and `SpliceCommand`, the parsed
  message: `spliceCommandItem.spliceEventId`, `outOfNetworkIndicator`, `breakDuration.duration` (90 kHz ticks,
  3456000 = 38.4 s), `breakDuration.autoReturn`, `spliceEventCancelIndicator`, `spliceImmediateFlag`,
  `uniqueProgramId`, `availNum`; plus `spliceDescriptorTypes`, `sapType`, `ptsAdjustment`.
- The decoder writes one empty row (all values null) when it starts — skip it.
- Markers are stored with a delay of up to a couple of minutes, and not always on every profile.

## Recordings (MultiRec)

Checked on MultiProbe 26.9 (one node, local storages).

- `Recording/Task/Find {}` — recording tasks: `taskID`, `monitoringObjectID` / `monitoringObjectName` (a service
  such as "Alaska", or a whole stream), `objectKind`, `nodeID`, `isDisabled`.
- `Recording/Storage/Find {}` — storages (`name`, `workingPath`). Segments lie on disk as
  `<workingPath>\<TASKID>\<guid>.ts`.
- `Recording/TimeLine/Find {taskID, timeBegin, timeEnd}` — intervals with `streamStatusType` (`data`, `signalloss`,
  `nowriting`, `notinit` …); the current interval has no `timeEnd`. Only `data` / `datawithAD` /
  `reservesourcedata` contain media.
- `Recording/DownloadTask/Add` cuts a piece `startDate`–`endDate` of one task. **`transcodingConfiguration` with
  `encoder`, `decoder`, `render` objects (empty is fine) is required** even for plain `download` / `stream`,
  otherwise 500 "Object reference not set…". Progress in `DownloadTask/Find` →
  `downloadTaskStateLog.downloadTaskState` / `downloadPercent`. Delete with `DownloadTask/Delete/{id}`.
  **MultiProbe removes a download task nobody extends after ~1.5 min**, even half-written (an 8 min `stream`
  piece vanished from `Find` at ~86 s with 174 s written). Its own player calls `POST
  DownloadTask/ExtendTask/{id}` (no body, empty answer) every 30 s; with that the piece is written to the end.
  - `download`: the original TS (as recorded: Alaska 1920x1080 H.264 + AAC) is written to
    `<workingPath>\Exporter Files\<downloadTaskID>\<name>.ts`; `url` = `/export/<id>/<name>.ts`, served at
    **`http://<mp>:40000/fromAppManager/<nodeID>/export/<id>/<name>`** (URL-encode the name; this is what the
    MultiProbe web UI uses). 1 min of Alaska took ~10 s. Deleting the task deletes the file a little later
    (seen up to ~1 min) and leaves the empty folder. With two tasks at once MultiProbe works them one
    after the other and may complete one **without `url`** (it never appears, `filePath` is set); the link
    `/export/<downloadTaskID>/<fileName>` works for it anyway.
  - A task on a whole stream (`objectKind: softwareStream`) records the whole multiplex (16 HD: ~30 Mbit/s,
    3.4 GB per 15 min) and there is no PID filter in `DownloadTask`. The channel's PIDs come from
    `MonitoringObject/Find`: `mpegService` children of the stream (`serviceID`), and their `mpegDecoder`
    children named "<service>; PID: 257; Audio" (`typeName` "Video Decoder" / "Audio Decoder").
  - The cut starts in the middle of a GOP (Alaska: GOP ~11 s): open it with `analyzeduration`/`probesize`
    well above the GOP, otherwise the frame size stays 0x0 and an MP4 header cannot be written; drop video
    packets before the first key frame.
  - `stream` + `downloadTaskStreamOutputType: hls` is a **preview**: MultiProbe re-encodes it to 640x360
    whatever `encoder` settings are passed (also with `streamWithTranscoding`). `url` =
    `Streams/<id>/playlist.m3u8`, served at **`http://<mp>:40000/fromNode/<nodeID>/Streams/<id>/playlist.m3u8`**
    (no auth needed). Master playlist with one video variant and a separate audio rendition (video-only and
    audio-only TS segments of 1 s), `#EXT-X-PLAYLIST-TYPE:EVENT`; `#EXT-X-ENDLIST` appears once the whole piece
    is written (state `completed` can come earlier). Produced about real time or faster;
    `PROGRAM-DATE-TIME` is the wall clock at generation, not the recording time. `DownloadTask/Rewind` moves it.
- On this test system the source loops a ~19 s clip: timestamps jump back (141.4 s -> 122.5 s) about every
  25 s of wall time, so a cut holds ~77 % of its length in media (15 min -> 11:30). MP4 needs growing
  timestamps: after a jump the Toolkit continues the time (one shift for all tracks, overlapping packets
  dropped). The HLS preview hides it by re-timing.
- The Toolkit makes MP4 by remuxing the `download` TS (no re-encoding) and MP3 by encoding its audio, both
  with PyAV (`av`, FFmpeg inside the wheel).
