"""Benchmark runner script.

Runs ContextMesh compression against test fixtures and collects metrics.
Generates a report with token reduction, latency, and chunk selection stats.

Usage:
    python contextmesh/benchmarks/run_benchmarks.py
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from contextmesh.benchmarks.metrics import BenchmarkMetrics
from contextmesh.core.chunker.base import CompressionInput
from contextmesh.core.pipeline import CompressionPipeline

logger = logging.getLogger(__name__)


TEST_CASES = [
    {
        "name": "large_python_file",
        "tool_name": "read_file",
        "task": "find all authentication-related functions and classes",
        "budget": 4000,
    },
    {
        "name": "json_response",
        "tool_name": "query_database",
        "task": "extract user information and settings",
        "budget": 3000,
    },
    {
        "name": "log_output",
        "tool_name": "run_shell",
        "task": "find error messages and their context",
        "budget": 2000,
    },
    {
        "name": "html_page",
        "tool_name": "web_scrape",
        "task": "extract the main article content",
        "budget": 3000,
    },
    {
        "name": "csv_data",
        "tool_name": "query_database",
        "task": "find rows with high values in the first numeric column",
        "budget": 2000,
    },
    {
        "name": "shell_output",
        "tool_name": "run_shell",
        "task": "find the command output that shows the error",
        "budget": 2000,
    },
]


def load_fixture(name: str) -> str:
    """Load a test fixture by name.

    Args:
        name: Fixture name.

    Returns:
        Fixture content.
    """
    fixtures_dir = Path(__file__).parent.parent.parent / "tests" / "fixtures"

    if name == "large_python_file":
        return (fixtures_dir / "large_python_file.py").read_text()

    if name == "json_response":
        return json.dumps({
            "users": [
                {
                    "id": i,
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "settings": {
                        "theme": "dark" if i % 2 else "light",
                        "notifications": i % 3 == 0,
                        "language": "en",
                    },
                    "permissions": ["read", "write"] if i % 2 else ["read"],
                    "metadata": {
                        "created_at": f"2024-01-{i+1:02d}",
                        "last_login": f"2024-06-{i+1:02d}",
                    },
                }
                for i in range(1, 101)
            ],
            "total": 100,
            "page": 1,
            "per_page": 100,
        }, indent=2)

    if name == "log_output":
        lines = []
        for i in range(200):
            level = "INFO" if i % 5 != 0 else "ERROR"
            if level == "ERROR":
                lines.append(
                    f"2024-06-18 10:{i//60:02d}:{i%60:02d} ERROR "
                    f"app.service - Failed to process request #{i}: "
                    f"ConnectionTimeout: upstream server did not respond"
                )
            else:
                lines.append(
                    f"2024-06-18 10:{i//60:02d}:{i%60:02d} INFO "
                    f"app.service - Processing request #{i}"
                )
        return "\n".join(lines)

    if name == "html_page":
        return """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<header><nav><ul><li>Home</li><li>About</li><li>Contact</li></ul></nav></header>
<main>
<article>
<h1>Main Article Title</h1>
<p>This is the main content of the article. It contains important information
that the agent needs to extract. The article discusses various topics
related to the task at hand.</p>
<section>
<h2>Section 1</h2>
<p>First section content with detailed information about the topic.</p>
</section>
<section>
<h2>Section 2</h2>
<p>Second section content with more details and examples.</p>
</section>
</article>
<aside>
<h3>Related Links</h3>
<ul><li>Link 1</li><li>Link 2</li><li>Link 3</li></ul>
</aside>
</main>
<footer><p>Copyright 2024</p></footer>
</body>
</html>"""

    if name == "csv_data":
        lines = ["id,name,value,category,status"]
        for i in range(1, 201):
            category = "A" if i % 3 == 0 else "B" if i % 3 == 1 else "C"
            status = "active" if i % 2 == 0 else "inactive"
            lines.append(f"{i},Item {i},{i * 10.5},{category},{status}")
        return "\n".join(lines)

    if name == "shell_output":
        return """$ ls -la /var/log
total 1024
drwxr-xr-x  5 root root  4096 Jun 18 10:00 .
drwxr-xr-x 12 root root  4096 Jun 18 09:00 ..
-rw-r--r--  1 root root 50000 Jun 18 10:00 syslog
-rw-r--r--  1 root root 25000 Jun 18 10:00 auth.log

$ cat /var/log/syslog | tail -20
Jun 18 10:00:01 server systemd[1]: Started Daily apt download activities.
Jun 18 10:00:02 server kernel: [12345.678] eth0: link up
Jun 18 10:00:03 server app[1234]: INFO: Processing request #1
Jun 18 10:00:04 server app[1234]: ERROR: Failed to connect to database
Jun 18 10:00:05 server app[1234]: INFO: Retrying connection...
Jun 18 10:00:06 server app[1234]: INFO: Connection established

$ df -h
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1       100G   45G   55G  45% /
tmpfs           8G    100M  7.9G   2% /tmp

$ ps aux | grep python
root      1234  0.5  2.1  123456  78901 ?  Ssl  10:00   0:05 python3 app.py
"""

    return f"Fixture content for {name}"


def run_benchmark() -> BenchmarkMetrics:
    """Run all benchmark test cases.

    Returns:
        Collected benchmark metrics.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    metrics = BenchmarkMetrics()
    pipeline = CompressionPipeline()

    logger.info(f"Running {len(TEST_CASES)} benchmark test cases")

    for i, case in enumerate(TEST_CASES, 1):
        logger.info(f"[{i}/{len(TEST_CASES)}] Running: {case['name']}")

        content = load_fixture(case["name"])
        original_tokens = len(content) // 4

        inp = CompressionInput(
            session_id="benchmark-session",
            task_id=f"benchmark-{case['name']}",
            tool_name=case["tool_name"],
            tool_args={},
            raw_output=content,
            task_description=case["task"],
            budget_tokens=case["budget"],
        )

        start_time = time.monotonic()
        result = pipeline.compress(inp)
        latency_ms = (time.monotonic() - start_time) * 1000

        metrics.add_result(
            compression_ratio=result.compression_ratio,
            latency_ms=latency_ms,
            outcome="success",
            tool_calls=1,
            chunks_selected=result.chunks_selected,
            chunks_total=result.chunks_total,
        )

        reduction = (1 - result.compression_ratio) * 100
        logger.info(
            f"  {case['name']}: {result.original_tokens} -> {result.compressed_tokens} tokens "
            f"({reduction:.1f}% reduction, {latency_ms:.1f}ms, "
            f"{result.chunks_selected}/{result.chunks_total} chunks)"
        )

    return metrics


def main() -> None:
    """Main entry point."""
    metrics = run_benchmark()

    print()
    print(metrics.report())

    output_dir = Path(__file__).parent.parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    summary = metrics.summary()
    with open(output_dir / "benchmark_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Results written to {output_dir / 'benchmark_results.json'}")


if __name__ == "__main__":
    main()
