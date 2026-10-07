"""JsonFormatter - see VIGIL.md PART 11.

The hasattr() check on optional fields is what prevents the ValueError that
broke logging in DocuMind. basicConfig must use force=True and a format string
that does NOT reference custom fields.
"""
