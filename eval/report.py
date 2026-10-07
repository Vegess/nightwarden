"""Prints the agent-vs-baseline comparison table.

Metrics: action accuracy, page precision, silent-degradation recall
(scenarios 4/6/7 only), median tool calls per decision, mean tokens per event,
p95 decision latency. Plus the raw confusion matrix.

Do not round numbers to flatter either side.
"""
