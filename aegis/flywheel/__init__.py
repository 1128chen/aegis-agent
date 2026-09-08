"""Flywheel: the self-optimization data pipeline for AegisAgent.

Trajectories captured by the runtime (see ``aegis.agentic_core`` trace sink
and ``aegis.store.llm_traces``) are turned into labelled SFT samples,
exported as a LoRA-friendly dataset, and consumed by the local trainer
(``flywheel.train``) whose adapters are served back to the runtime through
``flywheel.serve`` and registered in ``flywheel.registry``.
"""
from .datasets import SAMPLE_KINDS  # noqa: F401

FLYWHEEL_VERSION = "0.1.0"
