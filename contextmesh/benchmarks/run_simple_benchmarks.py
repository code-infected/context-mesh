"""Simple benchmark runner that tests chunkers without requiring sentence-transformers.

Runs ContextMesh chunkers against test fixtures and collects metrics.
Generates a report with chunk counts and token stats.

Usage:
    python contextmesh/benchmarks/run_simple_benchmarks.py
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

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


def run_benchmark() -> dict:
    """Run all benchmark test cases.

    Returns:
        Benchmark results dictionary.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    from contextmesh.core.chunker.code_chunker import CodeChunker
    from contextmesh.core.chunker.json_chunker import JSONChunker
    from contextmesh.core.chunker.log_chunker import LogChunker
    from contextmesh.core.chunker.html_chunker import HTMLChunker
    from contextmesh.core.chunker.csv_chunker import CSVChunker
    from contextmesh.core.chunker.shell_chunker import ShellChunker
    from contextmesh.core.tokenizer import TokenCounter

    tokenizer = TokenCounter.get_default()
    results = []

    chunkers = {
        "large_python_file": CodeChunker("python"),
        "json_response": JSONChunker(),
        "log_output": LogChunker(),
        "html_page": HTMLChunker(),
        "csv_data": CSVChunker(),
        "shell_output": ShellChunker(),
    }

    logger.info(f"Running {len(TEST_CASES)} benchmark test cases")

    for i, case in enumerate(TEST_CASES, 1):
        logger.info(f"[{i}/{len(TEST_CASES)}] Running: {case['name']}")

        content = load_fixture(case["name"])
        original_tokens = tokenizer.count(content)

        chunker = chunkers.get(case["name"])
        if chunker is None:
            logger.warning(f"No chunker for {case['name']}")
            continue

        start_time = time.monotonic()
        chunks = chunker.chunk(content)
        chunk_time_ms = (time.monotonic() - start_time) * 1000

        chunk_tokens = sum(c.token_count for c in chunks)
        chunk_types = {}
        for c in chunks:
            ct = c.chunk_type.value
            chunk_types[ct] = chunk_types.get(ct, 0) + 1

        result = {
            "name": case["name"],
            "tool_name": case["tool_name"],
            "original_tokens": original_tokens,
            "chunk_count": len(chunks),
            "chunk_tokens": chunk_tokens,
            "chunk_time_ms": chunk_time_ms,
            "chunk_types": chunk_types,
        }
        results.append(result)

        logger.info(
            f"  {case['name']}: {original_tokens} tokens -> {len(chunks)} chunks "
            f"({chunk_time_ms:.1f}ms, types: {chunk_types})"
        )

    return {"results": results}


def main() -> None:
    """Main entry point."""
    results = run_benchmark()

    print()
    print("=" * 60)
    print("ContextMesh Chunker Benchmark Report")
    print("=" * 60)

    total_tokens = 0
    total_chunks = 0
    total_time = 0

    for r in results["results"]:
        total_tokens += r["original_tokens"]
        total_chunks += r["chunk_count"]
        total_time += r["chunk_time_ms"]

        print(f"\n{r['name']}:")
        print(f"  Tokens: {r['original_tokens']}")
        print(f"  Chunks: {r['chunk_count']}")
        print(f"  Time: {r['chunk_time_ms']:.1f}ms")
        print(f"  Types: {r['chunk_types']}")

    print()
    print("=" * 60)
    print(f"Total: {total_tokens} tokens -> {total_chunks} chunks")
    print(f"Total Time: {total_time:.1f}ms")
    print(f"Avg Chunks per Case: {total_chunks / len(results['results']):.1f}")
    print("=" * 60)

    output_dir = Path(__file__).parent.parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    with open(output_dir / "chunker_benchmark.json", "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results written to {output_dir / 'chunker_benchmark.json'}")


if __name__ == "__main__":
    main()
