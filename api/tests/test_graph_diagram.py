"""docs/graph.mmd must match the graph the code actually builds."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_DIR = ROOT / "api"
sys.path.insert(0, str(API_DIR))


def _load_dump_graph():
    spec = importlib.util.spec_from_file_location("dump_graph", ROOT / "scripts" / "dump_graph.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_diagram_matches_the_compiled_graph():
    dump_graph = _load_dump_graph()
    committed = (ROOT / "docs" / "graph.mmd").read_text()
    assert committed == dump_graph.render(), "docs/graph.mmd is stale; run: python scripts/dump_graph.py"


def test_diagram_shows_the_structure_the_docs_describe():
    mermaid = (ROOT / "docs" / "graph.mmd").read_text()
    # The guardrail is the first node after the start, and the approval pause exists.
    assert "__start__ --> GuardrailMiddleware\\2ebefore_agent" in mermaid
    assert "HumanInTheLoopMiddleware" in mermaid
    assert "LoopCapMiddleware" in mermaid
    assert mermaid.count("-.->") >= 2, "expected conditional edges"
    assert len([line for line in mermaid.splitlines() if "(" in line and "\\2e" in line and "-->" not in line]) >= 5


def test_diagram_generation_needs_no_database():
    """It runs in CI and on a laptop without Postgres, so it must not open a connection."""
    dump_graph = _load_dump_graph()
    import copilot_agent

    opened = []
    real = copilot_agent._connect
    copilot_agent._connect = lambda *a, **k: opened.append(1) or real(*a, **k)
    try:
        dump_graph.render()
    finally:
        copilot_agent._connect = real
    assert opened == []
