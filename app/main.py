"""FastAPI app: logging config, route registration, startup checks.

Startup MUST log: llm_provider, llm_model, target_service_url, and whether the
provider is reachable. A provider that is unreachable should fail loudly here,
not silently at request time.
"""
