#!/usr/bin/env bash
set -euo pipefail

# Read-only RDS scoring post-mortem collector.
#
# Normal mode uses only read-only AWS APIs through aws_with_role.sh:
#   rds describe-db-instances / describe-db-log-files / download-db-log-file-portion
#   logs filter-log-events
#   cloudwatch get-metric-statistics
#
# Offline parser test:
#   ./scripts/rds_scoring_postmortem.sh --offline \
#     --timestamp 2026-09-29T21:00:24.503906Z \
#     --slow-log-file /tmp/interoves-post-0.log \
#     --slow-log-file /tmp/interoves-post-1.log

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/../venv/interoves_django/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing project Python: $PYTHON" >&2
  exit 2
fi

TEMP_ROOT="$(mktemp -d -t rds-scoring-postmortem.XXXXXX)"
cleanup() {
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

export RDS_POSTMORTEM_REPO_ROOT="$REPO_ROOT"
export RDS_POSTMORTEM_TMP_ROOT="$TEMP_ROOT"
"$PYTHON" - "$@" <<'PY'
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


DB_ID = "awseb-e-rkvpj3bv2a-stack-awsebrdsdatabase-xbcxyy6hynls"
REGION = "eu-central-1"
LOG_GROUP = "RDSOSMetrics"
ROLE = "./scripts/aws_with_role.sh"
SLOW_PREFIX = "slowquery/"
SQL_TIME_RE = re.compile(
    r"# Query_time:\s*([0-9.]+)\s+Lock_time:\s*([0-9.]+)\s+"
    r"Rows_sent:\s*(\d+)\s+Rows_examined:\s*(\d+)",
    re.I,
)
TIME_RE = re.compile(r"^# Time:\s*(\S+)", re.I)
COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.S)
STRING_RE = re.compile(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"")
NUMBER_RE = re.compile(r"(?<![A-Za-z_])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?![A-Za-z_])")
UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
ACTOR_KEY_RE = re.compile(
    r"\b(?:actor|user_id|anon_key|anonymous|team_id|user|uid|account)\b",
    re.I,
)


def utc_parse(value):
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include UTC timezone (use Z)")
    return parsed.astimezone(dt.timezone.utc)


def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def ms(value):
    return int(value.timestamp() * 1000)


def json_default(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


class Collector:
    def __init__(self, args):
        self.args = args
        self.errors = []
        self.api_calls = []

    def aws(self, service, *arguments):
        command = [ROLE, "aws", service, *arguments]
        self.api_calls.append(command[2:])
        try:
            completed = subprocess.run(
                command,
                cwd=Path(os.environ["RDS_POSTMORTEM_REPO_ROOT"]),
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as exc:
            self.errors.append(f"AWS {service} execution error: {exc}")
            return None
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            self.errors.append(f"AWS {service} API error: {message[-800:]}")
            return None
        try:
            return json.loads(completed.stdout or "null")
        except json.JSONDecodeError as exc:
            self.errors.append(f"AWS {service} returned invalid JSON: {exc}")
            return None


def normalize_sql(sql):
    sql = COMMENT_RE.sub(" ", sql)
    # MySQL slow-log boilerplate is not part of the statement fingerprint.
    sql = re.sub(r"\buse\s+[^;]+;", " ", sql, flags=re.I)
    sql = re.sub(r"\bset\s+timestamp\s*=\s*[^;]+;", " ", sql, flags=re.I)
    sql = UUID_RE.sub("?", sql)
    sql = STRING_RE.sub("?", sql)
    sql = NUMBER_RE.sub("?", sql)
    sql = re.sub(r"\s+", " ", sql).strip().lower()
    # Keep the fingerprint useful while never printing actor-like identifiers.
    sql = ACTOR_KEY_RE.sub("<actor_field>", sql)
    return sql


def sql_record(timestamp, query_time, lock_time, rows_sent, rows_examined, sql):
    normalized = normalize_sql(sql)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    lower = sql.lower()
    scoring = "row_number() over" in lower and "games_attempt" in lower
    return {
        "timestamp": timestamp,
        "query_time": query_time,
        "lock_time": lock_time,
        "rows_sent": rows_sent,
        "rows_examined": rows_examined,
        "scoring": scoring,
        "fingerprint": fingerprint,
        "normalized_sql": normalized,
    }


def parse_slow_log(text):
    records = []
    current = None
    sql_lines = []
    query_meta = None

    def finish():
        nonlocal current, sql_lines, query_meta
        if current is None or query_meta is None:
            current = None
            sql_lines = []
            query_meta = None
            return
        sql = " ".join(line.strip() for line in sql_lines if line.strip())
        if sql:
            records.append(sql_record(current, *query_meta, sql))
        current = None
        sql_lines = []
        query_meta = None

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        time_match = TIME_RE.match(line)
        if time_match:
            finish()
            try:
                current = utc_parse(time_match.group(1))
            except ValueError:
                current = None
            continue
        if current is None:
            continue
        meta = SQL_TIME_RE.search(line)
        if meta:
            query_meta = (
                float(meta.group(1)),
                float(meta.group(2)),
                int(meta.group(3)),
                int(meta.group(4)),
            )
            continue
        if line.startswith("#") or line.startswith("/usr/"):
            continue
        sql_lines.append(line)
    finish()
    return records


def read_slow_files(paths):
    records = []
    for path in paths:
        try:
            records.extend(parse_slow_log(Path(path).read_text(errors="replace")))
        except OSError as exc:
            records.append({"parse_error": f"{path}: {exc}"})
    return records


def epoch_bucket(timestamp):
    epoch = timestamp.timestamp()
    return dt.datetime.fromtimestamp(math.floor(epoch / 5) * 5, tz=dt.timezone.utc)


def median_or_none(values):
    return statistics.median(values) if values else None


def quantized(values, digits=3):
    return round(float(values), digits) if values is not None else None


def metric_period(start):
    age = dt.datetime.now(dt.timezone.utc) - start
    if age <= dt.timedelta(days=15):
        return 60
    if age <= dt.timedelta(days=63):
        return 300
    return 3600


def sample_phase(timestamp, incident, window, baseline=240):
    before_start = incident - dt.timedelta(seconds=window + baseline)
    before_end = incident - dt.timedelta(seconds=window)
    after_start = incident + dt.timedelta(seconds=window)
    after_end = incident + dt.timedelta(seconds=window + baseline)
    if before_start <= timestamp < before_end:
        return "before"
    if incident - dt.timedelta(seconds=window) <= timestamp < incident + dt.timedelta(seconds=window):
        return "incident"
    if after_start <= timestamp <= after_end:
        return "after"
    return "outside"


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_os_events(events):
    parsed = []
    for event in events or []:
        message = event.get("message", "") if isinstance(event, dict) else ""
        try:
            item = json.loads(message)
            timestamp = utc_parse(item["timestamp"])
            item["_timestamp"] = timestamp
            parsed.append(item)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return sorted(parsed, key=lambda item: item["_timestamp"])


def fetch_os_events(collector, start, end):
    result = collector.aws(
        "logs",
        "filter-log-events",
        "--region",
        REGION,
        "--log-group-name",
        LOG_GROUP,
        "--start-time",
        str(ms(start)),
        "--end-time",
        str(ms(end)),
        "--interleaved",
        "--output",
        "json",
    )
    return parse_os_events((result or {}).get("events", [])) if result else []


def fetch_cloudwatch(collector, start, end):
    metrics = [
        "CPUUtilization",
        "DatabaseConnections",
        "DiskQueueDepth",
        "EBSByteBalance%",
        "EBSIOBalance%",
        "FreeableMemory",
        "NetworkReceiveThroughput",
        "NetworkTransmitThroughput",
        "ReadIOPS",
        "ReadLatency",
        "ReadThroughput",
        "SwapUsage",
        "WriteIOPS",
        "WriteLatency",
        "WriteThroughput",
    ]
    period = metric_period(start)
    output = {name: [] for name in metrics}
    for name in metrics:
        result = collector.aws(
            "cloudwatch",
            "get-metric-statistics",
            "--region",
            REGION,
            "--namespace",
            "AWS/RDS",
            "--metric-name",
            name,
            "--dimensions",
            f"Name=DBInstanceIdentifier,Value={DB_ID}",
            "--statistics",
            "Average",
            "Maximum",
            "Minimum",
            "--period",
            str(period),
            "--start-time",
            iso(start),
            "--end-time",
            iso(end),
            "--output",
            "json",
        )
        if result:
            for point in result.get("Datapoints", []):
                point["_timestamp"] = utc_parse(point["Timestamp"])
            output[name] = sorted(result.get("Datapoints", []), key=lambda point: point["_timestamp"])
    return period, output


def download_slow_logs(collector, start, end, tmpdir):
    result = collector.aws(
        "rds",
        "describe-db-log-files",
        "--region",
        REGION,
        "--db-instance-identifier",
        DB_ID,
        "--output",
        "json",
    )
    if not result:
        return []
    selected = []
    margin = dt.timedelta(hours=1)
    for item in result.get("DescribeDBLogFiles", []):
        name = item.get("LogFileName", "")
        last_written = item.get("LastWritten")
        if not name.startswith(SLOW_PREFIX) or last_written is None:
            continue
        written = dt.datetime.fromtimestamp(last_written / 1000, tz=dt.timezone.utc)
        if start - margin <= written <= end + margin:
            selected.append(name)
    paths = []
    for index, name in enumerate(selected):
        path = Path(tmpdir) / f"slow-{index}.log"
        marker = None
        pending = True
        with path.open("w", encoding="utf-8") as output:
            while pending:
                arguments = [
                    "rds",
                    "download-db-log-file-portion",
                    "--region",
                    REGION,
                    "--db-instance-identifier",
                    DB_ID,
                    "--log-file-name",
                    name,
                    "--output",
                    "json",
                ]
                if marker:
                    arguments.extend(["--starting-token", marker])
                result = collector.aws(*arguments)
                if not result:
                    break
                output.write(result.get("LogFileData", ""))
                next_marker = result.get("Marker")
                pending = bool(result.get("AdditionalDataPending"))
                if pending and (not next_marker or next_marker == marker):
                    collector.errors.append(
                        f"RDS log pagination stopped for {name}: pending data without a new Marker"
                    )
                    break
                marker = next_marker
        if path.exists() and path.stat().st_size:
            paths.append(path)
    return paths


def filter_records(records, start, end):
    return [
        record
        for record in records
        if "timestamp" in record and start <= record["timestamp"] < end
    ]


def phase_values_os(events, incident, window):
    output = {"before": [], "incident": [], "after": []}
    for event in events:
        phase = sample_phase(event["_timestamp"], incident, window)
        if phase in output:
            output[phase].append(event)
    return output


def process_summary(events):
    grouped = {}
    for event in events:
        for process in event.get("processList", []):
            key = (process.get("name"), process.get("tgid"))
            bucket = grouped.setdefault(key, {"cpu": [], "rss": [], "memory": []})
            for field, target in (("cpuUsedPc", "cpu"), ("rss", "rss"), ("memoryUsedPc", "memory")):
                value = safe_float(process.get(field))
                if value is not None:
                    bucket[target].append(value)
    rows = []
    for (name, tgid), values in grouped.items():
        rows.append(
            {
                "name": name,
                "tgid": tgid,
                "max_cpu_pct": quantized(max(values["cpu"]) if values["cpu"] else None),
                "max_rss_kb": quantized(max(values["rss"]) if values["rss"] else None),
                "max_memory_pct": quantized(max(values["memory"]) if values["memory"] else None),
            }
        )
    return {
        "top_cpu": sorted(rows, key=lambda row: row["max_cpu_pct"] or -1, reverse=True)[:5],
        "top_rss": sorted(rows, key=lambda row: row["max_rss_kb"] or -1, reverse=True)[:5],
    }


def os_summary(events):
    fields = {
        "memory_free_kb": ("memory", "free"),
        "memory_cached_kb": ("memory", "cached"),
        "swap_free_kb": ("swap", "free"),
        "swap_in_raw_kb": ("swap", "in"),
        "swap_out_raw_kb": ("swap", "out"),
        "cpu_total_pct": ("cpuUtilization", "total"),
        "cpu_user_pct": ("cpuUtilization", "user"),
        "cpu_system_pct": ("cpuUtilization", "system"),
        "cpu_wait_pct": ("cpuUtilization", "wait"),
        "cpu_idle_pct": ("cpuUtilization", "idle"),
        "load_1": ("loadAverageMinute", "one"),
        "load_5": ("loadAverageMinute", "five"),
        "load_15": ("loadAverageMinute", "fifteen"),
    }
    output = {}
    for name, (group, field) in fields.items():
        values = [safe_float(event.get(group, {}).get(field)) for event in events]
        values = [value for value in values if value is not None]
        output[name] = {
            "count": len(values),
            "min": quantized(min(values)) if values else None,
            "median": quantized(statistics.median(values)) if values else None,
            "max": quantized(max(values)) if values else None,
        }
    output["samples"] = len(events)
    return output


def sql_buckets(records):
    buckets = {}
    for record in records:
        bucket = epoch_bucket(record["timestamp"])
        key = iso(bucket)
        item = buckets.setdefault(key, {"query_count": 0, "slow_query_count": 0, "scoring_count": 0, "times": [], "scoring_times": []})
        item["query_count"] += 1
        item["times"].append(record["query_time"])
        if record["query_time"] >= 2:
            item["slow_query_count"] += 1
        if record["scoring"]:
            item["scoring_count"] += 1
            item["scoring_times"].append(record["query_time"])
    output = []
    for bucket, item in sorted(buckets.items()):
        output.append(
            {
                "bucket": bucket,
                "query_count": item["query_count"],
                "slow_query_count": item["slow_query_count"],
                "scoring_count": item["scoring_count"],
                "max_query_time_s": quantized(max(item["times"])),
                "median_query_time_s": quantized(median_or_none(item["times"])),
            }
        )
    return output


def sql_report(records):
    return [
        {
            "timestamp": iso(record["timestamp"]),
            "query_time_s": record["query_time"],
            "lock_time_s": record["lock_time"],
            "rows_sent": record["rows_sent"],
            "rows_examined": record["rows_examined"],
            "scoring": record["scoring"],
            "fingerprint": record["fingerprint"],
            "normalized_sql": record["normalized_sql"],
        }
        for record in sorted(records, key=lambda item: item["timestamp"])
    ]


def print_report(report):
    print(json.dumps(report, indent=2, default=json_default, ensure_ascii=False))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="scripts/rds_scoring_postmortem.sh",
        description="Read-only RDS scoring post-mortem collector; no AWS write APIs are used."
    )
    parser.add_argument("--timestamp", required=True, help="Incident timestamp in UTC ISO8601, for example 2026-09-29T21:00:24Z")
    parser.add_argument("--window", type=int, default=60, help="Incident window in seconds on each side (default: 60)")
    parser.add_argument("--offline", action="store_true", help="Do not call AWS; parse only --slow-log-file inputs")
    parser.add_argument("--slow-log-file", action="append", default=[], help="Local slow log file; repeatable, useful for parser tests")
    return parser


def main():
    args = build_parser().parse_args()
    if args.window <= 0:
        raise SystemExit("--window must be positive")
    try:
        incident = utc_parse(args.timestamp)
    except ValueError as exc:
        raise SystemExit(f"invalid --timestamp: {exc}")

    collector = Collector(args)
    baseline = 240
    collection_start = incident - dt.timedelta(seconds=args.window + baseline)
    collection_end = incident + dt.timedelta(seconds=args.window + baseline)
    incident_start = incident - dt.timedelta(seconds=args.window)
    incident_end = incident + dt.timedelta(seconds=args.window)

    with tempfile.TemporaryDirectory(
        prefix="rds-scoring-postmortem-",
        dir=os.environ["RDS_POSTMORTEM_TMP_ROOT"],
    ) as tmpdir:
        slow_paths = [Path(path) for path in args.slow_log_file]
        if not args.offline and not slow_paths:
            slow_paths = download_slow_logs(collector, collection_start, collection_end, tmpdir)
        slow_records = read_slow_files(slow_paths)
        parse_errors = [item for item in slow_records if "parse_error" in item]
        slow_records = [item for item in slow_records if "timestamp" in item]
        incident_sql = filter_records(slow_records, incident_start, incident_end)

        os_events = []
        cloudwatch = {}
        cloudwatch_period = None
        if not args.offline:
            os_events = fetch_os_events(collector, collection_start, collection_end)
            cloudwatch_period, cloudwatch = fetch_cloudwatch(collector, collection_start, collection_end)

        phases = phase_values_os(os_events, incident, args.window)
        baseline_sql = {
            "before": filter_records(slow_records, collection_start, incident_start),
            "incident": incident_sql,
            "after": filter_records(slow_records, incident_end, collection_end),
        }

        report = {
            "report": "rds_scoring_postmortem",
            "read_only": True,
            "aws_write_apis_used": False,
            "incident_utc": iso(incident),
            "incident_window_utc": {"start": iso(incident_start), "end": iso(incident_end)},
            "baseline_windows_utc": {
                "before": {"start": iso(collection_start), "end": iso(incident_start)},
                "after": {"start": iso(incident_end), "end": iso(collection_end)},
            },
            "units": {
                "swap.in": "raw KB as reported by RDS; no rate conversion or delta",
                "swap.out": "raw KB as reported by RDS; no rate conversion or delta",
                "cpuUtilization.*": "percent",
                "diskIO.await": "milliseconds",
                "diskIO.avgQueueLen": "requests",
                "diskIO.util": "percent",
                "processList.rss": "KB",
                "processList.cpuUsedPc": "percent",
            },
            "data_availability": {
                "enhanced_monitoring_samples": len(os_events),
                "slow_log_files": [str(path) for path in slow_paths],
                "slow_log_records_incident": len(incident_sql),
                "cloudwatch_period_seconds": cloudwatch_period,
            },
            "errors": collector.errors + [f"slow log parse error: {item['parse_error']}" for item in parse_errors],
            "enhanced_monitoring": {
                "timeline": [
                    {
                        "timestamp": iso(event["_timestamp"]),
                        "phase": sample_phase(event["_timestamp"], incident, args.window),
                        "memory": event.get("memory", {}),
                        "swap": event.get("swap", {}),
                        "cpuUtilization": event.get("cpuUtilization", {}),
                        "loadAverageMinute": event.get("loadAverageMinute", {}),
                        "diskIO": event.get("diskIO", []),
                        "physicalDeviceIO": event.get("physicalDeviceIO", []),
                        "tasks": event.get("tasks", {}),
                    }
                    for event in os_events
                ],
                "summary_by_phase": {phase: os_summary(items) for phase, items in phases.items()},
                "top_processes_by_phase": {
                    phase: process_summary(items) for phase, items in phases.items()
                },
            },
            "cloudwatch_rds": {
                name: [
                    {
                        "timestamp": iso(point["_timestamp"]),
                        "average": point.get("Average"),
                        "minimum": point.get("Minimum"),
                        "maximum": point.get("Maximum"),
                        "unit": point.get("Unit"),
                        "phase": sample_phase(point["_timestamp"], incident, args.window),
                    }
                    for point in points
                ]
                for name, points in cloudwatch.items()
            },
            "slow_sql": {
                "query_count_semantics": "count of statements present in the slow log; this is not total database query volume",
                "all_queries_incident_window": sql_report(incident_sql),
                "scoring_queries_incident_window": sql_report([item for item in incident_sql if item["scoring"]]),
                "baseline_counts": {
                    phase: {
                        "query_count": len(items),
                        "slow_query_count_ge_2s": sum(item["query_time"] >= 2 for item in items),
                        "scoring_count": sum(item["scoring"] for item in items),
                        "max_query_time_s": max((item["query_time"] for item in items), default=None),
                        "median_query_time_s": median_or_none([item["query_time"] for item in items]),
                    }
                    for phase, items in baseline_sql.items()
                },
                "five_second_buckets": sql_buckets(incident_sql),
            },
            "interpretation": "Evidence only. No automatic root-cause classification is performed.",
        }
        print_report(report)
        if collector.errors:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
