## 2026-06-19 - [I/O Optimization in process_versions.py]
**Learning:** Writing thousands of small files on every execution is a major performance bottleneck, especially in CI environments. String comparison of JSON content before writing is faster than unconditional disk I/O. PR hygiene is critical: never commit profiling/benchmark scripts or massive data artifacts alongside code changes.
**Action:** Always implement conditional writes (check for changes) when dealing with large numbers of generated files. Use temporary scripts for verification but ensure they are deleted before submission.
