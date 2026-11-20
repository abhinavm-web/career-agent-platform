"""Import candidate-reported external applications into the private tracker."""
import csv
import hashlib
import io
import json

COLUMNS = ("Date applied", "Company", "Role", "Channel", "Status", "Evidence (email subject + date)")


def source_key(row):
    # Different evidence can identify separate applications with the same employer, title and date.
    parts = [row[k].strip().casefold() for k in COLUMNS if k != "Status"]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def pipeline_stage(status):
    value = status.casefold()
    for prefix, stage in (("rejected", "rejected"), ("closed", "closed"), ("withdrawn", "withdrawn"),
                          ("screening", "screening"), ("assessment", "assessment"), ("interview", "interview"),
                          ("under review", "under_review"), ("offer", "offer"), ("accepted", "accepted")):
        if value == prefix or value.startswith(prefix + " "):
            return stage
    return "applied"


def import_tracker(crm, content):
    if not isinstance(content, str) or len(content) > 1_000_000:
        raise ValueError("Choose a tracker CSV of up to 1 MB.")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
    if not reader.fieldnames or any(k not in reader.fieldnames for k in COLUMNS):
        raise ValueError("Required CSV headers: " + ", ".join(COLUMNS))
    counts = dict(rows=0, created=0, linked=0, updated=0, unchanged=0)
    conn = crm.conn
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        prior_links = {}
        for record in conn.execute("SELECT job_id,evidence_json FROM job_archives WHERE reason='tracker'"):
            previous = json.loads(record["evidence_json"]).get("tracker")
            if isinstance(previous, dict) and all(isinstance(previous.get(k), str) for k in COLUMNS):
                prior_links[source_key(previous)] = record["job_id"]
        try:
            for row in reader:
                if None in row or any(row[k] is None for k in COLUMNS):
                    raise ValueError(f"Check the columns on CSV line {reader.line_num}.")
                applied, company, role, channel, status, evidence = (row[k].strip() for k in COLUMNS)
                if not company or not applied:
                    raise ValueError(f"Company and date applied are required on CSV line {reader.line_num}.")
                key = source_key(row)
                counts["rows"] += 1
                existing = conn.execute("SELECT job_id,status FROM external_applications WHERE source_key=?", (key,)).fetchone()
                if existing and existing["status"] == status:
                    counts["unchanged"] += 1
                    continue
                if existing:
                    job_id = existing["job_id"]
                    conn.execute("UPDATE external_applications SET status=? WHERE source_key=?", (status, key))
                    counts["updated"] += 1
                else:
                    job_id = prior_links.get(key)
                    if job_id:
                        counts["linked"] += 1
                    else:
                        job_id = key[:16]
                        conn.execute("""INSERT INTO jobs(job_id,company,title,location,url,description,source,remote)
                            VALUES(?,?,?,'',?,'','external_tracker',0)""",
                            (job_id, company, role or "Role unspecified", "urn:career-agent:tracker:" + job_id))
                        counts["created"] += 1
                    conn.execute("""INSERT INTO external_applications
                        (source_key,job_id,company,role,applied_date,channel,status,evidence) VALUES(?,?,?,?,?,?,?,?)""",
                        (key, job_id, company, role, applied, channel, status, evidence))
                latest = conn.execute("SELECT actor,stage FROM crm_stage_events WHERE job_id=? ORDER BY event_id DESC LIMIT 1",
                                      (job_id,)).fetchone()
                stage = pipeline_stage(status)
                if latest is None or (latest["actor"] == "tracker_import" and latest["stage"] != stage):
                    conn.execute("INSERT INTO crm_stage_events(job_id,stage,note,actor) VALUES(?,?,?,'tracker_import')",
                                 (job_id, stage, status))
        except csv.Error as exc:
            raise ValueError(f"Invalid CSV on line {reader.line_num}.") from exc
    return counts
