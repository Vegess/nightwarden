"""Tables: events, deploys, incidents, decisions.

See VIGIL.md PART 4 for the schema.

decisions does double duty: audit trail for a human, and the evaluation dataset.
Keep expected_action populated from harness/labels.yaml so scoring is one query.
"""
