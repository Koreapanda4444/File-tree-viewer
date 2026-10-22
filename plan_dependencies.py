from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fs_paths import path_collision_key
from planning import ConflictPolicy, FilePlan, PlanAction


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    operation_id: str
    requires_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class DependencyCycle:
    operation_ids: tuple[str, ...]
    staged: bool


@dataclass(frozen=True, slots=True)
class DependencyProblem:
    operation_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class DependencyAnalysis:
    edges: tuple[DependencyEdge, ...]
    cycles: tuple[DependencyCycle, ...]
    problems: tuple[DependencyProblem, ...]
    execution_order: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems


def strongly_connected(nodes, graph):
    visited = set()
    ordered = []
    for root in nodes:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(graph.get(root, ())))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                ordered.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(graph.get(child, ()))))
    reverse = defaultdict(set)
    for node, children in graph.items():
        for child in children:
            reverse[child].add(node)
    visited.clear()
    for root in reversed(ordered):
        if root in visited:
            continue
        component = []
        stack = [root]
        visited.add(root)
        while stack:
            node = stack.pop()
            component.append(node)
            for child in reverse[node]:
                if child not in visited:
                    visited.add(child)
                    stack.append(child)
        if len(component) > 1:
            yield tuple(sorted(component))


def analyze_dependencies(plan: FilePlan) -> DependencyAnalysis:
    operations = [
        op for op in plan.operations if op.conflict_policy is not ConflictPolicy.SKIP
    ]
    sources = {}
    targets = defaultdict(list)
    folders = {}
    overwrites = {}
    by_id = {op.operation_id: op for op in operations}
    for op in operations:
        if op.source is not None and op.action is not PlanAction.COPY:
            sources.setdefault(path_collision_key(op.source), op)
        if op.target is not None:
            targets[path_collision_key(op.target)].append(op)
            if op.action is PlanAction.CREATE_FOLDER:
                folders[path_collision_key(op.target)] = op
            if op.conflict_policy is ConflictPolicy.OVERWRITE:
                overwrites[path_collision_key(op.target)] = op
    edges = set()
    problems = []
    for op in operations:
        if op.target is not None:
            vacating = sources.get(path_collision_key(op.target))
            if vacating and vacating.operation_id != op.operation_id:
                edges.add(
                    (
                        op.operation_id,
                        vacating.operation_id,
                        "vacate target before publishing",
                    )
                )
            for parent in op.target.parents:
                key = path_collision_key(parent)
                producer = folders.get(key)
                if producer and producer.operation_id != op.operation_id:
                    edges.add(
                        (
                            op.operation_id,
                            producer.operation_id,
                            "create destination folder",
                        )
                    )
                removed = sources.get(key)
                if (
                    removed
                    and removed.operation_id != op.operation_id
                    and producer is None
                ):
                    edges.add(
                        (
                            op.operation_id,
                            removed.operation_id,
                            "destination folder is removed or relocated",
                        )
                    )
        if op.source is not None:
            for parent in op.source.parents:
                overwrite = overwrites.get(path_collision_key(parent))
                if overwrite and overwrite.operation_id != op.operation_id:
                    problems.append(
                        DependencyProblem(
                            (op.operation_id, overwrite.operation_id),
                            "An overwritten folder also contains another planned source; separate these operations into different Plans",
                        )
                    )
    for writers in targets.values():
        if len(writers) > 1:
            problems.append(
                DependencyProblem(
                    tuple(op.operation_id for op in writers),
                    "Multiple operations produce the same path",
                )
            )
    graph = defaultdict(set)
    reasons = defaultdict(list)
    for node, required, reason in edges:
        graph[node].add(required)
        reasons[node].append((required, reason))
    cycles = []
    for component in strongly_connected(by_id, graph):
        members = set(component)
        staged = all(
            reason == "vacate target before publishing"
            for node in component
            for required, reason in reasons[node]
            if required in members
        ) and all(
            by_id[key].action in {PlanAction.MOVE, PlanAction.RENAME}
            for key in component
        )
        cycles.append(DependencyCycle(component, staged))
        if not staged:
            problems.append(
                DependencyProblem(
                    component,
                    "Cyclic destination-folder dependencies cannot be executed safely",
                )
            )

    def phase(op):
        return {
            PlanAction.DELETE: 0,
            PlanAction.CREATE_FOLDER: 1,
            PlanAction.MOVE: 2,
            PlanAction.RENAME: 3,
            PlanAction.CREATE_FILE: 4,
            PlanAction.COPY: 5,
        }[op.action]

    positions = {op.operation_id: i for i, op in enumerate(operations)}
    ordered = sorted(
        operations,
        key=lambda op: (
            phase(op),
            len(op.target.parts)
            if op.action is PlanAction.CREATE_FOLDER
            else positions[op.operation_id],
        ),
    )
    rank = {op.operation_id: i for i, op in enumerate(ordered)}
    for node, required, reason in edges:
        if reason == "create destination folder" and rank[required] >= rank[node]:
            problems.append(
                DependencyProblem(
                    (node, required),
                    "The required folder cannot be created before its dependent operation",
                )
            )
        elif reason == "destination folder is removed or relocated":
            problems.append(
                DependencyProblem(
                    (node, required),
                    "Destination folder is relocated or removed; use a separate Plan for operations inside its new location",
                )
            )
    return DependencyAnalysis(
        tuple(DependencyEdge(*edge) for edge in sorted(edges)),
        tuple(cycles),
        tuple(problems),
        tuple(op.operation_id for op in ordered),
    )
