"""Evidence for one synthetic incident, with the same interface as LiveEvidence."""
from datetime import datetime, timezone


class FixtureEvidence:
    def __init__(self, logs: list[dict], series: dict[str, list[float]], deploys: list[dict],
                 incidents: list[dict], step_min: int = 5):
        self.now = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
        self._logs, self._series, self._deploys, self._incidents = logs, series, deploys, incidents
        self._step = step_min

    def recent_logs(self, minutes, level=None, contains=None):
        rows = [r for r in self._logs if r["minutes_ago"] <= minutes]
        if level == "ERROR":
            rows = [r for r in rows if r["level"] in ("ERROR", "CRITICAL")]
        if contains:
            rows = [r for r in rows if contains.lower() in (r["msg"] + r.get("exc", "")).lower()]
        return sorted(rows, key=lambda r: -r["minutes_ago"])

    def metric_series(self, metric, minutes):
        values = self._series.get(metric, [])
        n = len(values)
        pts = [{"minutes_ago": (n - 1 - i) * self._step, "value": v} for i, v in enumerate(values)]
        return [p for p in pts if p["minutes_ago"] <= minutes]

    def deploys(self, hours):
        return [d for d in self._deploys if d["minutes_ago"] <= hours * 60]

    def past_incidents(self, query, limit):
        words = [w for w in query.lower().split() if len(w) > 2]
        rows = [i for i in self._incidents
                if not words or any(w in (i["summary"] + " " + i["kind"]).lower() for w in words)]
        return sorted(rows, key=lambda i: i["days_ago"])[:limit]
