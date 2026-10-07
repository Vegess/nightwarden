"""TOOLS schema (OpenAI function-calling format) and TOOL_IMPL dispatch.

Five tools - see VIGIL.md PART 5:
  get_recent_logs, get_metrics, get_deploy_history, search_past_incidents, decide

decide is terminal and has no implementation here; the orchestrator intercepts it.
"""
