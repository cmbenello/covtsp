"""LP relaxation lower bound for Covering TSP.

Provides two formulations:
1. Static graph LP (primary): flow-based LP on the collapsed static graph
2. Time-expanded LP (stretch): tighter bound on the full time-expanded graph
"""

import pulp
import networkx as nx

from src.graph.network import TransitNetwork
from src.graph.time_expanded import TimeExpandedGraph


def compute_lp_bound(
    static_network: TransitNetwork,
    required_stations: set[str],
    start_station: str,
) -> dict:
    """Compute LP relaxation lower bound on the Covering TSP.

    Uses a min-cost flow formulation on the static graph:
    - Variables: x_{ij} >= 0 (flow on each directed edge)
    - Objective: minimize sum of w_{ij} * x_{ij}
    - Constraints:
      1. Flow conservation at each intermediate node
      2. Coverage: flow into each required station >= 1
      3. Start station has net outflow >= 1

    Args:
        static_network: Static transit network with min travel times.
        required_stations: Set of station IDs that must be visited.
        start_station: Starting station ID.

    Returns:
        Dict with lp_bound_seconds, status, and solver details.
    """
    G = static_network.graph
    stations = set(G.nodes) & required_stations
    edges = list(G.edges(data=True))

    if not stations or not edges:
        return {"lp_bound_seconds": 0, "status": "infeasible", "detail": "empty graph"}

    # Create LP problem
    prob = pulp.LpProblem("CoveringTSP_LPBound", pulp.LpMinimize)

    # Decision variables: flow on each edge
    x = {}
    for u, v, data in edges:
        var_name = f"x_{u}_{v}".replace("-", "m").replace(" ", "_")
        x[(u, v)] = pulp.LpVariable(var_name, lowBound=0, cat="Continuous")

    # Objective: minimize total weighted flow
    prob += pulp.lpSum(
        data["weight"] * x[(u, v)]
        for u, v, data in edges
        if (u, v) in x
    )

    # Constraint 1: Flow conservation at each non-start node
    # flow_in - flow_out = 0 for intermediate, >= 0 for terminals
    all_nodes = set()
    for u, v, _ in edges:
        all_nodes.add(u)
        all_nodes.add(v)

    for node in all_nodes:
        in_flow = pulp.lpSum(
            x[(u, v)] for u, v, _ in edges if v == node and (u, v) in x
        )
        out_flow = pulp.lpSum(
            x[(u, v)] for u, v, _ in edges if u == node and (u, v) in x
        )

        if node == start_station:
            # Start station: net outflow >= 1
            prob += out_flow - in_flow >= 1, f"start_outflow_{node}"
        else:
            # Other nodes: flow conservation (in >= out, allowing flow to terminate)
            prob += in_flow >= out_flow, f"conservation_{node}"

    # Constraint 2: Coverage — each required station must be visited
    for sid in stations:
        if sid == start_station:
            continue  # start is automatically visited
        in_flow = pulp.lpSum(
            x[(u, v)] for u, v, _ in edges if v == sid and (u, v) in x
        )
        prob += in_flow >= 1, f"coverage_{sid}"

    # Solve
    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    status = pulp.LpStatus[prob.status]
    bound = pulp.value(prob.objective) if prob.status == pulp.constants.LpStatusOptimal else None

    # Collect non-zero flows for analysis
    active_edges = {}
    if bound is not None:
        for (u, v), var in x.items():
            val = var.varValue
            if val and val > 1e-6:
                active_edges[f"{u} -> {v}"] = round(val, 3)

    return {
        "lp_bound_seconds": int(bound) if bound is not None else None,
        "status": status,
        "active_edges": len(active_edges),
        "detail": f"LP solved with {len(x)} variables, {len(prob.constraints)} constraints",
    }


def compute_lp_bound_time_expanded(
    teg: TimeExpandedGraph,
    required_stations: set[str],
    start_station: str,
    start_time: int = 0,
    max_variables: int = 50000,
) -> dict:
    """Compute LP relaxation on the time-expanded graph (tighter bound).

    Same flow formulation but on the full time-expanded graph.
    May be intractable for large instances.

    Args:
        teg: Time-expanded graph.
        required_stations: Set of station IDs to cover.
        start_station: Starting station ID.
        start_time: Earliest departure time.
        max_variables: Skip if graph has more edges than this.

    Returns:
        Dict with lp_bound_seconds, status, and solver details.
    """
    G = teg.graph

    if G.number_of_edges() > max_variables:
        return {
            "lp_bound_seconds": None,
            "status": "skipped",
            "detail": f"Graph has {G.number_of_edges()} edges, exceeds max_variables={max_variables}",
        }

    edges = list(G.edges(data=True))
    prob = pulp.LpProblem("CoveringTSP_TEG_LP", pulp.LpMinimize)

    # Decision variables
    x = {}
    for i, (u, v, data) in enumerate(edges):
        x[(u, v)] = pulp.LpVariable(f"x_{i}", lowBound=0, cat="Continuous")

    # Objective
    prob += pulp.lpSum(
        data["weight"] * x[(u, v)]
        for u, v, data in edges
        if (u, v) in x
    )

    # Find valid start nodes
    start_nodes = set(teg.get_start_nodes(start_station, start_time))

    # Build node sets for constraints
    all_nodes = set()
    for u, v, _ in edges:
        all_nodes.add(u)
        all_nodes.add(v)

    # Index edges by node for efficiency
    in_edges = {n: [] for n in all_nodes}
    out_edges = {n: [] for n in all_nodes}
    for u, v, data in edges:
        out_edges[u].append((u, v))
        in_edges[v].append((u, v))

    # Flow conservation
    for node in all_nodes:
        in_flow = pulp.lpSum(x[e] for e in in_edges[node] if e in x)
        out_flow = pulp.lpSum(x[e] for e in out_edges[node] if e in x)

        if node in start_nodes:
            prob += out_flow - in_flow >= 0, f"start_{node[0]}_{node[1]}"
        else:
            prob += in_flow >= out_flow, f"cons_{node[0]}_{node[1]}"

    # At least one start node must have outflow
    prob += pulp.lpSum(
        x[e] for sn in start_nodes for e in out_edges.get(sn, []) if e in x
    ) >= 1, "start_outflow"

    # Coverage: each required station must have flow into at least one of its time nodes
    for sid in required_stations:
        if sid == start_station:
            continue
        station_nodes = [n for n in all_nodes if n[0] == sid]
        if station_nodes:
            total_in = pulp.lpSum(
                x[e] for n in station_nodes for e in in_edges.get(n, []) if e in x
            )
            prob += total_in >= 1, f"cover_{sid}"

    # Solve
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=60))

    status = pulp.LpStatus[prob.status]
    bound = pulp.value(prob.objective) if prob.status == pulp.constants.LpStatusOptimal else None

    return {
        "lp_bound_seconds": int(bound) if bound is not None else None,
        "status": status,
        "detail": f"TEG LP: {len(x)} variables, {len(prob.constraints)} constraints",
    }


def compute_optimality_gap(heuristic_seconds: int, lp_bound_seconds: int) -> float:
    """Compute optimality gap as a percentage.

    gap = (heuristic - bound) / bound * 100

    A gap of 5% means no algorithm can improve the heuristic by more than 5%.
    """
    if lp_bound_seconds <= 0:
        return float("inf")
    return (heuristic_seconds - lp_bound_seconds) / lp_bound_seconds * 100
