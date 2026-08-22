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
