"""Download and extract GTFS feeds."""

import hashlib
import zipfile
from pathlib import Path

import requests
from rich.console import Console

console = Console()

REQUIRED_FILES = ["stops.txt", "stop_times.txt", "trips.txt", "routes.txt", "calendar.txt"]
OPTIONAL_FILES = ["calendar_dates.txt", "transfers.txt"]


def download_gtfs(url: str, dest_dir: str | Path, force: bool = False) -> Path:
    """Download a GTFS zip and extract to dest_dir.

    Args:
        url: URL of the GTFS zip file.
        dest_dir: Directory to extract into.
        force: Re-download even if data exists.

    Returns:
        Path to the extracted GTFS directory.
    """
    dest_dir = Path(dest_dir)
    marker = dest_dir / ".downloaded"

    if marker.exists() and not force:
        console.print(f"[dim]GTFS data already exists at {dest_dir}, skipping download[/dim]")
        return dest_dir

    if not url:
        raise ValueError(f"No GTFS URL configured. Place GTFS files manually in {dest_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "gtfs.zip"

    console.print(f"[bold]Downloading GTFS feed from {url}...[/bold]")
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    sha256 = hashlib.sha256()

    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            sha256.update(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                console.print(f"\r  {downloaded / 1e6:.1f} MB / {total / 1e6:.1f} MB ({pct:.0f}%)", end="")

    console.print(f"\n[green]Downloaded {downloaded / 1e6:.1f} MB (sha256: {sha256.hexdigest()[:12]})[/green]")

    console.print("Extracting...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    zip_path.unlink()

    # Validate required files
    missing = [f for f in REQUIRED_FILES if not (dest_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"GTFS feed missing required files: {missing}")

    present = [f for f in REQUIRED_FILES + OPTIONAL_FILES if (dest_dir / f).exists()]
    console.print(f"[green]Extracted: {', '.join(present)}[/green]")

    marker.write_text(sha256.hexdigest())
    return dest_dir
