"""
Analyze server request logs (e.g. logs.txt from the TP5 social media API)
to extract metrics that help design and calibrate a global load test.

Usage:
    python analyze_logs.py [logfile]        # default: logs.txt

The script is stdlib-only and auto-detects UTF-16/UTF-8 encoding, so it
works with the raw file captured through PowerShell redirection.

Log format expected (one line per request):
    [2025-09-04 20:23:14][INFO][creative_beaver] GET /feed

Metrics produced:
  - global request frequency (avg / peak requests per second)
  - per-second timeline (exported to timeline.csv for plotting)
  - endpoint frequency + normalized mix (for Locust task weights)
  - per-user activity (number of virtual users to simulate)
  - think time per user (for Locust wait_time calibration)
"""

import csv
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

LINE_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"   # timestamp (1s resolution)
    r"\[(\w+)\]"                                   # level
    r"\[([^\]]+)\]"                                # user
    r"\s(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s"  # HTTP method
    r"(\S+)"                                       # path
)

DIGITS_RE = re.compile(r"\d+")


def read_text(path: str) -> str:
    """Read the log handling UTF-16LE (PowerShell) and UTF-8 encodings."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def normalize(path: str) -> str:
    """Collapse numeric ids: /like/15 -> /like/{id}."""
    return DIGITS_RE.sub("{id}", path)


def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * p / 100
    low, high = int(idx), min(int(idx) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (idx - low)


def fmt_pct(part, total):
    return f"{100 * part / total:5.1f}%" if total else "  0.0%"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "logs.txt"
    text = read_text(path)

    entries = []  # (datetime, user, method, raw_path)
    skipped = 0
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if not m:
            skipped += 1
            continue
        ts, _level, user, method, raw_path = m.groups()
        entries.append((datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"), user, method, raw_path))

    if not entries:
        print(f"No request lines matched in {path!r}")
        return

    entries.sort(key=lambda e: e[0])
    total = len(entries)
    first, last = entries[0][0], entries[-1][0]
    window = (last - first).total_seconds() + 1  # +1: inclusive second buckets
    duration = (last - first).total_seconds()

    print("=" * 62)
    print(f"Log file          : {path}")
    print(f"Request lines     : {total}  (non-matching lines: {skipped})")
    print(f"Window            : {first:%H:%M:%S} -> {last:%H:%M:%S}  ({duration:.0f}s)")
    print(f"Avg request rate  : {total / window:.2f} req/s   ({total / window * 60:.1f} req/min)")

    # ---- per-second timeline ------------------------------------------------
    per_second = Counter(e[0] for e in entries)
    counts = [per_second.get(datetime.fromtimestamp(t), 0)
              for t in range(int(first.timestamp()), int(last.timestamp()) + 1)]
    seconds = len(counts)
    peak = max(counts)
    print("-" * 62)
    print("Timeline (req/s)  : avg {:.2f} | median {:.0f} | p95 {:.0f} | peak {}".format(
        total / seconds, statistics.median(counts), percentile(counts, 95), peak))
    busy = sorted(per_second.items(), key=lambda kv: kv[1], reverse=True)[:5]
    print("Busiest seconds   : " + ", ".join(f"{ts:%H:%M:%S}={c}" for ts, c in busy))

    with open("timeline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "requests"])
        for ts, c in sorted(per_second.items()):
            w.writerow([ts.strftime("%H:%M:%S"), c])

    # ---- endpoint frequency -------------------------------------------------
    by_endpoint = Counter()
    by_normalized = Counter()
    by_method = Counter()
    for _, _, method, raw_path in entries:
        by_endpoint[(method, raw_path)] += 1
        by_method[method] += 1
        by_normalized[(method, normalize(raw_path))] += 1

    print("-" * 62)
    print("Endpoint mix (normalized) -- use as Locust task weights:")
    for (method, path), c in by_normalized.most_common():
        print(f"  {method:6} {path:12} {c:5}  {fmt_pct(c, total)}")

    hot_raw = sorted(by_endpoint.items(), key=lambda kv: -kv[1])[:5]
    print("Hottest concrete endpoints:")
    for (method, path), c in hot_raw:
        print(f"  {method:6} {path:12} {c:5}  {fmt_pct(c, total)}")

    print("Methods           : " + ", ".join(f"{m}={c}" for m, c in by_method.most_common()))

    # ---- per-user activity --------------------------------------------------
    by_user = Counter(e[1] for e in entries)
    per_user_times = defaultdict(list)
    for ts, user, _, _ in entries:
        per_user_times[user].append(ts)

    print("-" * 62)
    print(f"Distinct users    : {len(by_user)} (incl. anonymous) -- Locust user-count hint")
    print("User activity (share of traffic, req/min):")
    for user, c in by_user.most_common():
        span = max((per_user_times[user][-1] - per_user_times[user][0]).total_seconds(), 1)
        print(f"  {user:20} {c:5}  {fmt_pct(c, total)}  {c / span * 60:5.1f} req/min")

    # ---- think time (gap between consecutive requests of the same user) -----
    gaps = []
    for user, times in per_user_times.items():
        if user == "anonymous":
            continue  # logins are startup noise, not user behaviour
        for a, b in zip(times, times[1:]):
            gaps.append((b - a).total_seconds())
    if gaps:
        print("-" * 62)
        print("Per-user think time (seconds between 2 requests of the same user):")
        print("  n={} | mean {:.2f}s | median {:.0f}s | p90 {:.0f}s | max {:.0f}s".format(
            len(gaps), statistics.mean(gaps), statistics.median(gaps),
            percentile(gaps, 90), max(gaps)))
        print("  (timestamps have 1s resolution -> treat these as a floor/quantized values)")

    # ---- Locust calibration summary -----------------------------------------
    app_traffic = sum(c for (m, p), c in by_normalized.items() if p != "/login")
    print("-" * 62)
    print("Locust calibration hints:")
    print(f"  - users           : ~{len(by_user) - 1} (distinct app users seen)")
    print(f"  - wait_time       : between({max(statistics.median(gaps) - 2, 0):.0f}, "
          f"{statistics.median(gaps) + 2:.0f})  # from observed think time")
    print(f"  - target rate     : {total / window:.1f} req/s ~= {users_needed(total / window, statistics.median(gaps))} users at median think time")
    print("  - task weights    : exclude /login (done once per Locust user in on_start)")
    print("  - data gaps       : no latency/status codes in logs (access_log=False);")
    print("                      measure those with Locust stats during the test itself")
    print("=" * 62)


def users_needed(rate_per_s, think_s):
    if think_s <= 0:
        think_s = 1
    return max(1, round(rate_per_s * think_s))


if __name__ == "__main__":
    main()
