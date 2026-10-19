"""Dataset adapters.

Adapters convert external rows into the Radar Lab event schema. They are
standard-library only so the demo server can import them. None of them read a
label to build a feature: `label` is attached only for evaluation.
"""
