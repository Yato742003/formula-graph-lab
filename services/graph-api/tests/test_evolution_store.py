"""Durable-shape checks for E1 campaign append/replay boundaries."""

import pytest

from app.problem_spec import ProblemDefinition
from app.research_store import IdempotencyConflictError, Neo4jResearchStore
from tests.test_problem_spec import make_valid_definition_payload


@pytest.mark.asyncio
async def test_campaign_state_is_scoped_idempotent_and_never_restarted():
    store = Neo4jResearchStore(driver=None)
    definition = ProblemDefinition.model_validate(make_valid_definition_payload())
    spec, _ = await store.freeze_problem_spec(
        workspace_id="ws-evolution-store",
        actor_id="researcher",
        actor_role="researcher",
        idempotency_key="spec-key",
        definition=definition,
    )
    campaign, replayed = await store.start_evolution_campaign(
        workspace_id=spec.workspace_id,
        spec_id=spec.spec_id,
        actor_id="researcher",
        idempotency_key="start-key",
    )
    assert replayed is False

    reserved, replayed = await store.reserve_evolution_generation(
        workspace_id=spec.workspace_id,
        evolution_id=campaign.evolution_id,
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        idempotency_key="reserve-key",
    )
    assert replayed is False
    assert len(reserved.reservations) == 1

    same, replayed = await store.reserve_evolution_generation(
        workspace_id=spec.workspace_id,
        evolution_id=campaign.evolution_id,
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        idempotency_key="reserve-key",
    )
    assert replayed is True
    assert same == reserved
    with pytest.raises(IdempotencyConflictError):
        await store.reserve_evolution_generation(
            workspace_id=spec.workspace_id,
            evolution_id=campaign.evolution_id,
            candidate_slots=2,
            compute_reserved=1,
            actor_id="controller",
            idempotency_key="reserve-key",
        )

    current, reused = await store.start_evolution_campaign(
        workspace_id=spec.workspace_id,
        spec_id=spec.spec_id,
        actor_id="researcher",
        idempotency_key="new-start-key",
    )
    assert reused is True
    assert current == reserved
    assert await store.get_evolution_campaign(
        workspace_id="another-workspace", evolution_id=campaign.evolution_id
    ) is None

    stopped, replayed = await store.stop_evolution_campaign(
        workspace_id=spec.workspace_id,
        evolution_id=campaign.evolution_id,
        actor_id="researcher",
        idempotency_key="stop-key",
    )
    assert replayed is False
    assert stopped.status == "stopped"
    assert stopped.reservations[0].status == "cancelled"
    page, total = await store.list_evolution_campaigns(workspace_id=spec.workspace_id)
    assert total == 1
    assert page == [stopped]
