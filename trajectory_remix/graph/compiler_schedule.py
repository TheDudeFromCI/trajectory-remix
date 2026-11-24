"""Dependency scheduling shared by structural dry runs and token compilation."""

from collections import defaultdict, deque

from .models import GraphEvent, GraphSource


def is_model_event(event: GraphEvent) -> bool:
    return event.kind in ("agent_text", "tool_call")


class EventSchedule:
    def __init__(self, source: GraphSource):
        self.source = source
        self.dependencies = {
            ident: tuple(dict.fromkeys(event.after))
            for ident, event in sorted(source.events.items())
        }

    def ready(self, ident, done, tick):
        event = self.source.events[ident]
        dependencies = self.dependencies[ident]
        if not all(dep in done for dep in dependencies):
            return False
        return all(
            completed_at < tick
            or (
                completed_at == tick
                and not was_model
                and (is_model_event(event) or event.delivery.chain_immediately)
            )
            for completed_at, was_model in (done[dep] for dep in dependencies)
        )

    def causal_order(self):
        """Resolve dependency order and ancestors without assigning ticks or durations."""
        known = {
            ident: tuple(sorted(dep for dep in deps if dep in self.dependencies))
            for ident, deps in self.dependencies.items()
        }
        children = defaultdict(list)
        indegree = {ident: len(deps) for ident, deps in known.items()}
        for ident, deps in known.items():
            for dep in deps:
                children[dep].append(ident)

        ready = deque(ident for ident, degree in indegree.items() if degree == 0)
        ordered, ancestors = [], {}
        while ready:
            ident = ready.popleft()
            ordered.append(ident)
            ancestors[ident] = set(known[ident])
            for dep in known[ident]:
                ancestors[ident].update(ancestors[dep])
            for child in children[ident]:
                indegree[child] -= 1
                if not indegree[child]:
                    ready.append(child)

        blocked = {ident for ident, degree in indegree.items() if degree}
        return ordered, ancestors, _cycle_members(blocked, known, children)


def _cycle_members(blocked, dependencies, children):
    """Find strongly connected components among events the schedule cannot reach."""
    visited, finished = set(), []
    for root in sorted(blocked):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(dependencies[root]))]
        while stack:
            ident, edges = stack[-1]
            dep = next(edges, None)
            if dep is None:
                finished.append(ident)
                stack.pop()
            elif dep in blocked and dep not in visited:
                visited.add(dep)
                stack.append((dep, iter(dependencies[dep])))

    cyclic = set()
    remaining = blocked.copy()
    for root in reversed(finished):
        if root not in remaining:
            continue
        remaining.remove(root)
        component = [root]
        for ident in component:
            for child in children[ident]:
                if child in remaining:
                    remaining.remove(child)
                    component.append(child)
        if len(component) > 1 or root in dependencies[root]:
            cyclic.update(component)
    return sorted(cyclic)
