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
