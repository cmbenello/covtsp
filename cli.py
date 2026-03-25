"""CLI entry point for the Open Transit Optimizer."""

from dotenv import load_dotenv
load_dotenv()

from datetime import date, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.config import load_config
from src.gtfs.download import download_gtfs
from src.gtfs.parser import GTFSParser
from src.backtest import backtest

console = Console()


@click.group()
def cli():
    """Open Transit Optimizer — Covering TSP solver for transit networks."""
    pass


@cli.command()
@click.option("--config", "-c", required=True, help="Path to city config YAML")
@click.option("--force", is_flag=True, help="Re-download even if data exists")
def download(config, force):
    """Download GTFS data for a city."""
    cfg = load_config(config)
    download_gtfs(cfg.gtfs_url, cfg.data_dir, force=force)


@cli.command()
@click.option("--config", "-c", required=True, help="Path to city config YAML")
@click.option("--date", "-d", "target_date", required=True, help="Target date (YYYY-MM-DD)")
@click.option("--output", "-o", default=None, help="Output JSON path")
@click.option("--lookahead", default=3, help="Greedy solver lookahead depth")
@click.option("--iterations", default=500, help="Local search iterations")
@click.option("--teg-lp", is_flag=True, help="Also compute time-expanded LP bound")
@click.option("--solver", default="greedy", type=click.Choice(["greedy", "segment", "static", "sweep"]), help="Solver type (sweep = fast NN from every station)")
@click.option("--run-mode", is_flag=True, help="Use running speed for transfers (overrides config)")
@click.option("--run-speed", default=None, type=float, help="Running speed in km/h (overrides config)")
def solve(config, target_date, output, lookahead, iterations, teg_lp, solver, run_mode, run_speed):
    """Run the full solver on a city's GTFS data for a given date."""
    cfg = load_config(config)

    if run_mode:
        cfg.movement_mode = "run"
    if run_speed is not None:
        cfg.running_speed_kmh = run_speed
        cfg.movement_mode = "run"

    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        console.print(f"[red]Invalid date format: {target_date}. Use YYYY-MM-DD.[/red]")
        return

    if output is None:
        output = f"results/{cfg.gtfs_path}_{target_date}.json"

    results = backtest(
        cfg, dt,
        output_path=output,
        lookahead=lookahead,
        local_search_iterations=iterations,
        compute_teg_lp=teg_lp,
        solver_type=solver,
    )

    if "error" in results:
        console.print(f"[red]Error: {results['error']}[/red]")


@cli.command()
@click.option("--config", "-c", required=True, help="Path to city config YAML")
def validate(config):
    """Validate that GTFS data parses correctly."""
    cfg = load_config(config)

    if not cfg.data_dir.exists():
        console.print(f"[red]GTFS data not found at {cfg.data_dir}[/red]")
        console.print("Run 'download' first.")
        return

    console.print(f"[bold]Validating GTFS data for {cfg.city_name}...[/bold]")

    parser = GTFSParser(cfg)
    try:
        parsed = parser.parse()
    except Exception as e:
        console.print(f"[red]Parse error: {e}[/red]")
        return

    table = Table(title="GTFS Validation")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Stations", str(len(parsed.stations)))
    table.add_row("Trip segments", str(len(parsed.segments)))
    table.add_row("Walking transfers", str(len(parsed.walking_transfers)))
    table.add_row("Required stations", str(len(parsed.required_station_ids)))
    table.add_row("Expected stations", str(cfg.station_count))

    missing = cfg.station_count - len(parsed.required_station_ids)
    if missing > 0:
        table.add_row("Missing stations", f"[yellow]{missing}[/yellow]")
    else:
        table.add_row("Coverage", "[green]Complete[/green]")

    console.print(table)


@cli.command()
@click.option("--config", "-c", required=True, help="Path to city config YAML")
def info(config):
    """Show information about a city's configuration and data."""
    cfg = load_config(config)

    table = Table(title=f"{cfg.city_name} Configuration")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("City", cfg.city_name)
    table.add_row("GTFS path", str(cfg.data_dir))
    table.add_row("GTFS URL", cfg.gtfs_url or "(local only)")
    table.add_row("Expected stations", str(cfg.station_count))
    table.add_row("Route type filter", str(cfg.route_type_filter))
    table.add_row("Walking speed", f"{cfg.walking_speed_kmh} km/h")
    table.add_row("Running speed", f"{cfg.running_speed_kmh} km/h")
    table.add_row("Movement mode", cfg.movement_mode)
    table.add_row("Effective speed", f"{cfg.effective_speed_kmh} km/h")
    table.add_row("Max transfer distance", f"{cfg.max_walk_distance_m} m")
    table.add_row("Start station", cfg.start_station or "(auto)")
    table.add_row("Time window", f"{cfg.time_window.start} - {cfg.time_window.end}")
    table.add_row("Data exists", str(cfg.data_dir.exists()))

    console.print(table)


if __name__ == "__main__":
    cli()
