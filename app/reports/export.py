"""CSV and XML export of the alarm history report."""
import csv
import io
import xml.etree.ElementTree as ET
from datetime import datetime

from .alarms import AlarmReport, AlarmRow

ALARM_COLUMNS = [
    ("col_group", lambda r: r.group),
    ("col_priority", lambda r: r.priority),
    ("col_alarm", lambda r: r.alarm),
    ("col_condition", lambda r: r.condition),
    ("col_stream", lambda r: r.stream),
    ("col_object", lambda r: r.object),
    ("col_node", lambda r: r.node),
    ("col_detected", lambda r: fmt_dt(r.detected)),
    ("col_finished", lambda r: fmt_dt(r.finished)),
    ("col_duration", lambda r: fmt_duration(r.duration_sec)),
]


def fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def fmt_duration(seconds: int) -> str:
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    hms = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {hms}" if days else hms


def status_text(row: AlarmRow, t) -> str:
    return t("status_active") if row.active else t("status_finished")


def alarms_csv(report: AlarmReport, t, delimiter: str) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([t(key) for key, _ in ALARM_COLUMNS] + [t("col_status"), t("col_confirmed")])
    for r in report.rows:
        writer.writerow(
            [get(r) for _, get in ALARM_COLUMNS]
            + [status_text(r, t), t("yes") if r.confirmed else t("no")]
        )
    # BOM so that Excel opens UTF-8 (Cyrillic names) correctly
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def availability_csv(report: AlarmReport, t, delimiter: str) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([
        t("col_stream"), t("col_object"), t("col_kind"), t("tl_outage"), t("tl_degraded"),
        t("col_availability") + " %", t("col_incidents"), t("col_longest"),
    ])
    stream = ""
    for a in report.availability:
        if a.depth == 0:
            stream = a.name
        writer.writerow([
            stream, a.name, a.kind_name, fmt_duration(a.outage_sec), fmt_duration(a.degraded_sec),
            f"{a.availability:.2f}".replace(".", "," if delimiter == ";" else "."),
            a.incidents, fmt_duration(a.longest_sec),
        ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def alarms_xml(report: AlarmReport, meta: dict) -> bytes:
    """Own structure: <AlarmHistoryReport> with <Parameters>, <Summary> and <Alarms>.
    Times are ISO 8601 with the UTC offset of the user's time zone; durations in seconds."""
    root = ET.Element(
        "AlarmHistoryReport",
        {
            "version": "1",
            "generated": report.generated.astimezone(meta["tz"]).isoformat(timespec="seconds"),
            "timeZone": meta["time_zone"],
        },
    )
    params = ET.SubElement(root, "Parameters")
    ET.SubElement(params, "User").text = meta["user"]
    ET.SubElement(params, "Server", {"id": str(report.params["node_id"])}).text = meta["server"]
    ET.SubElement(
        params,
        "Period",
        {"from": meta["period"][0].isoformat(timespec="seconds"), "to": meta["period"][1].isoformat(timespec="seconds")},
    )
    ET.SubElement(params, "ObjectCount").text = str(len(report.params["object_ids"]))

    s = report.summary
    summary = ET.SubElement(
        root,
        "Summary",
        {
            "total": str(s["total"]),
            "active": str(s["active"]),
            "objects": str(s["objects"]),
            "objectsWithOutage": str(s["objects_with_outage"]),
            "availabilityPercent": f'{s["availability"]:.2f}',
        },
    )
    for name, count in s["by_group"]:
        ET.SubElement(summary, "Group", {"name": name, "count": str(count)})

    avail = ET.SubElement(
        root,
        "ServiceAvailability",
        {"outageMinPriority": str(meta["outage_min"]), "degradedMinPriority": str(meta["degraded_min"])},
    )
    for a in report.availability:
        el = ET.SubElement(
            avail,
            "Object",
            {
                "id": a.object_id,
                "kind": a.kind,
                "depth": str(a.depth),
                "outageSec": str(a.outage_sec),
                "degradedSec": str(a.degraded_sec),
                "availabilityPercent": f"{a.availability:.2f}",
                "incidents": str(a.incidents),
                "longestSec": str(a.longest_sec),
            },
        )
        el.text = a.name

    alarms = ET.SubElement(root, "Alarms")
    for r in report.rows:
        el = ET.SubElement(
            alarms,
            "Alarm",
            {
                "group": r.group,
                "priority": str(r.priority),
                "status": "active" if r.active else "finished",
                "confirmed": "true" if r.confirmed else "false",
            },
        )
        ET.SubElement(el, "Name").text = r.alarm
        ET.SubElement(el, "Condition").text = r.condition
        ET.SubElement(el, "Stream").text = r.stream
        ET.SubElement(el, "Object", {"id": r.object_id}).text = r.object
        ET.SubElement(el, "Node").text = r.node
        ET.SubElement(el, "Detected").text = r.detected.isoformat(timespec="seconds")
        if r.finished:
            ET.SubElement(el, "Finished").text = r.finished.isoformat(timespec="seconds")
        ET.SubElement(el, "DurationSec").text = str(r.duration_sec)

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# --- Measurement report --------------------------------------------------------

def fmt_num(value, decimal_comma: bool = False, digits: int = 4) -> str:
    """Up to `digits` decimals, no trailing zeros; empty for missing values."""
    if value is None:
        return ""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".") if abs(value) < 1e15 else f"{value:.0f}"
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", ",") if decimal_comma else text


def measures_csv(report, t, delimiter: str) -> bytes:
    """Long format: one line per object, measure and time bucket."""
    comma = delimiter == ";"
    raw = report.resolution == "raw"
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    values = [t("col_value")] if raw else [t("col_min"), t("col_avg"), t("col_max")]
    writer.writerow([t("col_time"), t("col_stream"), t("col_object"), t("col_analyzer"), t("col_measure"), t("col_unit")] + values)
    for s in report.series:
        for p in s.points:
            if p.avg is None:
                continue
            nums = [p.avg] if raw else [p.min, p.avg, p.max]
            writer.writerow([fmt_dt(p.time), s.stream, s.object, s.analyzer, s.measure, s.display_unit]
                            + [fmt_num(s.d(n), comma) for n in nums])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def measures_summary_csv(report, t, delimiter: str) -> bytes:
    comma = delimiter == ";"
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([t("col_stream"), t("col_object"), t("col_analyzer"), t("col_measure"), t("col_unit"),
                     t("col_min"), t("col_avg"), t("col_max"), t("col_std"), t("col_coverage") + " %"])
    for s in report.series:
        st = s.stats
        writer.writerow([s.stream, s.object, s.analyzer, s.measure, s.display_unit]
                        + [fmt_num(s.d(st.get(k)), comma) for k in ("min", "avg", "max", "std")]
                        + [fmt_num(st["coverage"], comma) if st.get("coverage") is not None else ""])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def measures_xml(report, meta: dict) -> bytes:
    raw = report.resolution == "raw"
    root = ET.Element("MeasurementReport", {
        "version": "1",
        "generated": report.generated.astimezone(meta["tz"]).isoformat(timespec="seconds"),
        "timeZone": meta["time_zone"],
        "resolution": report.resolution,
    })
    params = ET.SubElement(root, "Parameters")
    ET.SubElement(params, "User").text = meta["user"]
    ET.SubElement(params, "Server", {"id": str(report.params["node_id"])}).text = meta["server"]
    ET.SubElement(params, "Period", {
        "from": report.period[0].isoformat(timespec="seconds"),
        "to": report.period[1].isoformat(timespec="seconds"),
    })
    for s in report.series:
        el = ET.SubElement(root, "Series", {
            "objectId": s.object_id, "object": s.object, "stream": s.stream,
            "analyzerId": str(s.analyzer_id), "analyzer": s.analyzer,
            "column": s.column, "measure": s.measure, "unit": s.unit,
        })
        st = s.stats
        ET.SubElement(el, "Statistics", {k: fmt_num(st.get(k)) for k in ("min", "avg", "max", "std")}
                      | {"points": str(st.get("points", 0))}
                      | ({"coveragePercent": fmt_num(st["coverage"])} if st.get("coverage") is not None else {}))
        points = ET.SubElement(el, "Points")
        for p in s.points:
            if p.avg is None:
                continue
            attrs = {"time": p.time.isoformat(timespec="seconds")}
            attrs |= {"value": fmt_num(p.avg)} if raw else {"min": fmt_num(p.min), "avg": fmt_num(p.avg), "max": fmt_num(p.max)}
            ET.SubElement(points, "Point", attrs)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# --- SCTE-35 marker report --------------------------------------------------------------

def _yes_no(value, t) -> str:
    return "" if value is None else (t("yes") if value else t("no"))


def scte_row(m, t) -> dict:
    """One marker as displayed (page table, CSV)."""
    return {
        "time": m.time.strftime("%Y-%m-%d %H:%M:%S.") + f"{m.time.microsecond // 1000:03d}",
        "stream": m.stream,
        "object": m.object,
        "command": m.command,
        "event_id": "" if m.event_id is None else str(m.event_id),
        "direction": {"out": t("scte_out"), "in": t("scte_in")}.get(m.direction, ""),
        "duration": "" if m.duration_sec is None else fmt_num(m.duration_sec, False, 3),
        "auto_return": _yes_no(m.auto_return, t),
        "cancel": t("yes") if m.cancel else "",
        "segmentation": m.segmentation,
    }


SCTE_COLUMNS = [("col_time", "time"), ("col_stream", "stream"), ("col_object", "object"),
                ("col_command", "command"), ("col_event_id", "event_id"), ("col_direction", "direction"),
                ("col_break_sec", "duration"), ("col_auto_return", "auto_return"), ("col_cancel", "cancel"),
                ("col_segmentation", "segmentation")]


def scte_csv(report, t, delimiter: str) -> bytes:
    comma = delimiter == ";"
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([t(key) for key, _ in SCTE_COLUMNS])
    for m in report.markers:
        row = scte_row(m, t)
        if comma:
            row["duration"] = row["duration"].replace(".", ",")
        writer.writerow([row[field] for _, field in SCTE_COLUMNS])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def scte_xml(report, meta: dict) -> bytes:
    """<Scte35Report> with <Summary>, <Missing> markers and the <Markers> themselves."""
    root = ET.Element("Scte35Report", {
        "version": "1",
        "generated": report.generated.astimezone(meta["tz"]).isoformat(timespec="seconds"),
        "timeZone": meta["time_zone"],
    })
    params = ET.SubElement(root, "Parameters")
    ET.SubElement(params, "User").text = meta["user"]
    ET.SubElement(params, "Server", {"id": str(report.params["node_id"])}).text = meta["server"]
    ET.SubElement(params, "Period", {"from": report.period[0].isoformat(timespec="seconds"),
                                     "to": report.period[1].isoformat(timespec="seconds")})
    s = report.summary
    summary = ET.SubElement(root, "Summary", {
        "markers": str(s["markers"]), "breaks": str(s["breaks"]),
        "breakTotalSec": fmt_num(s["break_total_sec"]), "missing": str(s["missing"]),
    })
    for name, count in s["by_command"]:
        ET.SubElement(summary, "Command", {"name": name, "count": str(count)})
    for o in s["per_object"]:
        ET.SubElement(summary, "Object", {
            "id": o["id"], "stream": o["stream"], "markers": str(o["markers"]), "breaks": str(o["breaks"]),
            "breakSec": fmt_num(o["break_sec"]), "missed": str(o["missed"]),
        }).text = o["name"]
    missing = ET.SubElement(root, "Missing")
    for x in report.missing:
        el = ET.SubElement(missing, "Marker", {
            "time": x["time"].isoformat(timespec="seconds"), "stream": x["stream"], "command": x["command"],
            **({"eventId": str(x["event_id"])} if x["event_id"] is not None else {}),
        })
        for name in x["missing"]:
            ET.SubElement(el, "MissingOn").text = name
    markers = ET.SubElement(root, "Markers")
    for m in report.markers:
        attrs = {"time": m.time.isoformat(timespec="milliseconds"), "objectId": m.object_id, "object": m.object,
                 "stream": m.stream, "command": m.command}
        if m.command_type is not None:
            attrs["commandType"] = str(m.command_type)
        if m.event_id is not None:
            attrs["eventId"] = str(m.event_id)
        if m.out_of_network is not None:
            attrs["outOfNetwork"] = "true" if m.out_of_network else "false"
        if m.duration_sec is not None:
            attrs["breakDurationSec"] = fmt_num(m.duration_sec)
        if m.auto_return is not None:
            attrs["autoReturn"] = "true" if m.auto_return else "false"
        if m.cancel:
            attrs["cancel"] = "true"
        if m.segmentation:
            attrs["segmentationTypes"] = m.segmentation
        ET.SubElement(markers, "Marker", attrs)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
