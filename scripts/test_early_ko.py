"""Test early-morning K.O. strategies: prefix visits, start station variants."""

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver

KO_ID = "940GZZLUKOY"
EC_ID = "940GZZLUECT"  # Earl's Court
EALING_ID = "940GZZLUEBY"  # Ealing Broadway
T4_ID = "940GZZLUHR4"  # Heathrow Terminal 4
HATTON_CROSS_ID = "940GZZLUHNX"  # Hatton Cross

config = load_config("configs/london.yaml")
target_date = date(2026, 3, 24)

print("Parsing GTFS...")
parser = GTFSParser(config)
parsed = parser.parse(target_date)
required = parsed.required_station_ids
n_required = len(required)
print(f"Required stations: {n_required}")

print("Building TEG...")
t0 = time.time()
teg = TimeExpandedGraph.from_gtfs(parsed)
print(f"TEG: {teg.node_count} nodes, {teg.edge_count} edges ({time.time()-t0:.1f}s)")

solver = GreedySolver(teg, lookahead=1)

def fmt(route):
    h = route.total_time_seconds // 3600
    m = (route.total_time_seconds % 3600) // 60
    return f"{route.stations_visited}/{n_required} {h}h{m:02d}m"

def missed(route):
    visited = {v["station_id"] for v in route.visits}
    return required - visited

t4_inj = [(T4_ID, [HATTON_CROSS_ID], 600)]

# === BASELINES ===
print("\n=== BASELINES ===")
route_nn = solver.solve_fast(EALING_ID, required, 6*3600+33*60)
print(f"  k=1 NN baseline:      {fmt(route_nn)}")
route_urg = solver.solve_with_injections(EALING_ID, required, 6*3600+33*60, urgency_weight=0.5, injections=t4_inj)
print(f"  Urgency+T4:           {fmt(route_urg)}  missed: {[parsed.stations[s].name for s in missed(route_urg)]}")

# === PREFIX: Visit K.O. first, then urgency NN ===
print("\n=== PREFIX K.O. FIRST (Ealing Broadway start) ===")
for start_min in range(350, 420, 3):
    start_sec = start_min * 60
    h, m = start_min // 60, start_min % 60

    start_nodes = teg.get_start_nodes(EALING_ID, start_sec)
    if not start_nodes:
        continue

    route = solver.solve_with_injections(
        EALING_ID, required, start_sec,
        urgency_weight=0.5,
        injections=t4_inj,
        prefix_stations=[KO_ID],
    )

    if route.stations_visited >= n_required - 2:
        missed_stations = missed(route)
        total_h = route.total_time_seconds // 3600
        total_m = (route.total_time_seconds % 3600) // 60
        ko_ok = KO_ID not in missed_stations
        t4_ok = T4_ID not in missed_stations
        marker = " ***" if route.stations_visited == n_required else ""
        missed_names = [parsed.stations[s].name.replace(" Underground Station", "") for s in sorted(missed_stations) if s in parsed.stations]
        print(
            f"  {h:02d}:{m:02d} → {route.stations_visited}/{n_required} "
            f"{total_h}h{total_m:02d}m  K.O.={'Y' if ko_ok else 'N'}  "
            f"T4={'Y' if t4_ok else 'N'}{marker}"
            f"{'  missed: ' + ', '.join(missed_names) if missed_names else ''}"
        )

# === PREFIX: Visit K.O. first from nearby stations ===
print("\n=== PREFIX K.O. FROM NEARBY STARTS ===")
nearby_starts = [
    (EC_ID, "Earl's Court"),
    ("940GZZLUHSK", "High St Ken"),
    ("940GZZLUWBN", "West Brompton"),
    ("940GZZLUBSC", "Barons Court"),
    ("940GZZLUACT", "Acton Town"),
    ("940GZZLUSBC", "Shepherd's Bush C"),
    ("940GZZLUSBM", "Shepherd's Bush M"),
]

# Check actual IDs
for sid, name in nearby_starts:
    if sid not in parsed.stations:
        print(f"  WARNING: {name} ({sid}) not in stations!")
        continue

for sid, name in nearby_starts:
    if sid not in parsed.stations:
        continue
    for start_min in range(340, 420, 3):
        start_sec = start_min * 60
        h, m = start_min // 60, start_min % 60

        start_nodes = teg.get_start_nodes(sid, start_sec)
        if not start_nodes:
            continue

        route = solver.solve_with_injections(
            sid, required, start_sec,
            urgency_weight=0.5,
            injections=t4_inj,
            prefix_stations=[KO_ID],
        )

        if route.stations_visited >= n_required - 1:
            missed_stations = missed(route)
            total_h = route.total_time_seconds // 3600
            total_m = (route.total_time_seconds % 3600) // 60
            marker = " ***" if route.stations_visited == n_required else ""
            missed_names = [parsed.stations[s].name.replace(" Underground Station", "") for s in sorted(missed_stations) if s in parsed.stations]
            print(
                f"  {name} @ {h:02d}:{m:02d} → {route.stations_visited}/{n_required} "
                f"{total_h}h{total_m:02d}m{marker}"
                f"{'  missed: ' + ', '.join(missed_names) if missed_names else ''}"
            )

# === SWEEP: Try ALL stations with K.O. prefix ===
print("\n=== FULL SWEEP WITH K.O. PREFIX (best results only) ===")
best_272 = None
best_271 = None
all_stations = sorted(required)
sweep_times = list(range(5*3600, 8*3600+1, 3*60))  # every 3 min

for gs in all_stations:
    for t in sweep_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue

        route = solver.solve_with_injections(
            gs, required, t,
            urgency_weight=0.5,
            injections=t4_inj,
            prefix_stations=[KO_ID],
        )

        if route.stations_visited == n_required:
            if best_272 is None or route.total_time_seconds < best_272[0].total_time_seconds:
                best_272 = (route, gs, t)
                name = parsed.stations[gs].name.replace(" Underground Station", "")
                h, m = t // 3600, (t % 3600) // 60
                total_h = route.total_time_seconds // 3600
                total_m = (route.total_time_seconds % 3600) // 60
                print(f"  272/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m ***")
        elif route.stations_visited == n_required - 1:
            if best_271 is None or route.total_time_seconds < best_271[0].total_time_seconds:
                best_271 = (route, gs, t)

if best_272:
    r, gs, t = best_272
    name = parsed.stations[gs].name.replace(" Underground Station", "")
    h, m = t // 3600, (t % 3600) // 60
    total_h = r.total_time_seconds // 3600
    total_m = (r.total_time_seconds % 3600) // 60
    print(f"\n  BEST 272/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m")
else:
    print("\n  No 272/272 found with K.O. prefix")

if best_271:
    r, gs, t = best_271
    name = parsed.stations[gs].name.replace(" Underground Station", "")
    h, m = t // 3600, (t % 3600) // 60
    total_h = r.total_time_seconds // 3600
    total_m = (r.total_time_seconds % 3600) // 60
    missed_stations = missed(r)
    missed_names = [parsed.stations[s].name.replace(" Underground Station", "") for s in sorted(missed_stations)]
    print(f"  BEST 271/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m  missed: {', '.join(missed_names)}")

# === NO PREFIX: urgency sweep for comparison ===
print("\n=== URGENCY SWEEP (no prefix, T4 injection only) — best results ===")
best_272_noprefix = None
best_271_noprefix = None

for gs in all_stations:
    for t in sweep_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue

        route = solver.solve_with_injections(
            gs, required, t,
            urgency_weight=0.5,
            injections=t4_inj,
        )

        if route.stations_visited == n_required:
            if best_272_noprefix is None or route.total_time_seconds < best_272_noprefix[0].total_time_seconds:
                best_272_noprefix = (route, gs, t)
                name = parsed.stations[gs].name.replace(" Underground Station", "")
                h, m = t // 3600, (t % 3600) // 60
                total_h = route.total_time_seconds // 3600
                total_m = (route.total_time_seconds % 3600) // 60
                print(f"  272/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m ***")
        elif route.stations_visited == n_required - 1:
            if best_271_noprefix is None or route.total_time_seconds < best_271_noprefix[0].total_time_seconds:
                best_271_noprefix = (route, gs, t)

if best_272_noprefix:
    r, gs, t = best_272_noprefix
    name = parsed.stations[gs].name.replace(" Underground Station", "")
    h, m = t // 3600, (t % 3600) // 60
    total_h = r.total_time_seconds // 3600
    total_m = (r.total_time_seconds % 3600) // 60
    print(f"\n  BEST 272/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m")
else:
    print("\n  No 272/272 found with urgency sweep")

if best_271_noprefix:
    r, gs, t = best_271_noprefix
    name = parsed.stations[gs].name.replace(" Underground Station", "")
    h, m = t // 3600, (t % 3600) // 60
    total_h = r.total_time_seconds // 3600
    total_m = (r.total_time_seconds % 3600) // 60
    missed_stations = missed(r)
    missed_names = [parsed.stations[s].name.replace(" Underground Station", "") for s in sorted(missed_stations)]
    print(f"  BEST 271/272: {name} @ {h:02d}:{m:02d} → {total_h}h{total_m:02d}m  missed: {', '.join(missed_names)}")
