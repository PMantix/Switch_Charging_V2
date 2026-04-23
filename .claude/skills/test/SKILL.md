---
name: test
description: Run the full test suite (unit + integration) and report failures with diagnosis
disable-model-invocation: true
allowed-tools: Bash(python *)
---

Run the project test suite and report results:

1. Run `python3 -m unittest discover -s tests -v` from the project root
2. If all tests pass, report the count and total time
3. If any tests fail:
   - Show the exact failure output
   - Read the failing test code to understand what it expects
   - Read the source code being tested to identify the root cause
   - Suggest a fix (but don't apply it without confirmation)
4. Check for import errors or missing dependencies separately from test failures
