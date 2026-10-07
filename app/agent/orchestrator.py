"""Tool-calling loop - see VIGIL.md PART 7.

max_rounds default 6. Every tool call recorded with round, name, args.
If max_rounds is exhausted without a decide call, record action=notify,
confidence=low, with a rationale saying triage was inconclusive.
It must NEVER silently return a default.
"""
