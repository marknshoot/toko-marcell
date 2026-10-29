import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import simulate_traffic as traffic  # noqa: E402


def _simulation(seed: int = 42, sessions: int = 100):
    return traffic.generate_simulation(
        seed=seed,
        sessions=sessions,
        catalog=traffic.FALLBACK_CATALOG,
        catalog_source="embedded fallback",
        anchor=datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
    )


def test_dry_run_generation_is_deterministic_and_valid():
    first = _simulation()
    second = _simulation()

    assert first.stats() == second.stats()
    assert first.events == second.events
    assert first.orders == second.orders
    assert first.turns == second.turns
    assert first.feedback == second.feedback

    stats = first.stats()
    assert stats["sessions"] == 100
    assert stats["orders"] == 18
    assert stats["revenue_idr"] == 9_618_000
    assert stats["copilot_turns"] == 63
    assert stats["assisted_sessions"] == 55
    assert stats["outfit_budget_violations"] == 0
    assert {
        name: cohort["sessions"] for name, cohort in stats["cohorts"].items()
    } == {"baseline": 45, "sizing": 25, "policy": 15, "outfit": 15}
    traffic.validate_simulation(first)


def test_all_ids_are_sim_prefixed_and_referentially_sound():
    sim = _simulation()

    assert len(sim.session_cohorts) == 100
    assert set(sim.session_cohorts) == {
        f"sim_sess_{index:04d}" for index in range(1, 101)
    }
    assert all(session_id.startswith("sim_sess_") for session_id in sim.session_cohorts)
    assert all(event.session_id.startswith("sim_sess_") for event in sim.events)
    assert all(event.session_id in sim.session_cohorts for event in sim.events)
    assert all(order.token.startswith("sim_ord_") for order in sim.orders)
    assert all(order.session_id in sim.session_cohorts for order in sim.orders)
    assert all(turn.thread_id.startswith("sim_thr_") for turn in sim.turns)
    assert all(feedback.thread_id.startswith("sim_thr_") for feedback in sim.feedback)


def test_order_and_outfit_invariants_hold():
    sim = _simulation()

    for order in sim.orders:
        assert order.item_count == sum(item.qty for item in order.items)
        assert order.total_idr == sum(
            item.qty * item.unit_price_idr for item in order.items
        )

    outfit_orders = [
        order
        for order in sim.orders
        if sim.session_cohorts[order.session_id] == "outfit"
    ]
    assert outfit_orders
    for order in outfit_orders:
        assert order.item_count >= 2
        assert order.total_idr <= sim.outfit_budgets[order.session_id]


def test_cli_dry_run_writes_nothing_and_is_repeatable(monkeypatch, capsys):
    monkeypatch.setattr(
        traffic,
        "load_catalog",
        lambda db_url: (traffic.FALLBACK_CATALOG, "embedded fallback"),
    )

    def fail_write(**kwargs):
        raise AssertionError("dry-run must not call write_simulation")

    monkeypatch.setattr(traffic, "write_simulation", fail_write)

    args = ["--dry-run", "--sessions", "100", "--seed", "42"]
    assert traffic.main(args) == 0
    first = capsys.readouterr().out
    assert "Generated sessions              100" in first
    assert "Dry run: no database rows written." in first

    assert traffic.main(args) == 0
    second = capsys.readouterr().out
    assert first == second
