"""pydantic-settings Settings.

NEVER use os.getenv anywhere in this project. pydantic-settings loads .env into
the Settings object, not into os.environ - os.getenv will return None and a
hardcoded default will silently win.

Required: groq_api_key, groq_model, database_url, target_service_url,
target_metrics_token, debug (bool, default False).

Missing required config must raise at startup, not degrade to a default.
"""
