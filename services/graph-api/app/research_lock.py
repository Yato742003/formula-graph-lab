"""One transaction lock for source/analysis writers and research snapshot consumers."""


async def lock_research_workspace(tx, group_id: str) -> None:
    # ponytail: serialize writes per workspace, ceiling: contended write throughput,
    # upgrade: finer locking after measured workspace contention.
    result = await tx.run(
        "MERGE (w:ResearchWorkspaceLock {group_id: $group}) "
        "SET w.revision = coalesce(w.revision, 0) + 1 RETURN w.revision",
        group=group_id,
    )
    await result.consume()
