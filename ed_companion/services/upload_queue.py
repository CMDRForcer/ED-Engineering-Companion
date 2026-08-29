from copy import deepcopy


def normalize_upload_queue(value):
    """Validate persisted jobs and recover work interrupted while sending."""
    if not isinstance(value, list):
        return []
    jobs = []
    for item in value:
        if not isinstance(item, dict):
            continue
        job = deepcopy(item)
        if job.get("status") == "sending":
            job["status"] = "retry"
            job.setdefault("recovered_after_restart", True)
        jobs.append(job)
    return jobs


def compact_upload_queue(jobs, limit=2000):
    """Limit receipts without ever discarding unsent or failed work."""
    valid = [deepcopy(item) for item in jobs or [] if isinstance(item, dict)]
    pending = [item for item in valid if item.get("status") != "sent"]
    if len(pending) >= limit:
        return pending
    sent = [item for item in valid if item.get("status") == "sent"]
    return sent[-(limit - len(pending)):] + pending


def latest_delivery_proof(jobs):
    """Return a payload-free proof for the latest accepted upload."""
    job = next((
        item for item in reversed(jobs or [])
        if isinstance(item, dict) and item.get("status") == "sent"
    ), None)
    if not job:
        return {}
    prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
    message = (
        prepared.get("message")
        if isinstance(prepared.get("message"), dict) else {}
    )
    receipt = job.get("receipt") if isinstance(job.get("receipt"), dict) else {}
    result = str(job.get("last_result") or "")
    if not result and receipt.get("httpStatus") is not None:
        result = f"Gateway accepted HTTP {receipt['httpStatus']}"
    return {
        "sentAt": str(job.get("sent_at") or ""),
        "schema": str(prepared.get("schema") or ""),
        "eventName": str(message.get("event") or ""),
        "stationName": str(message.get("stationName") or ""),
        "timestamp": str(message.get("timestamp") or ""),
        "result": result,
    }
