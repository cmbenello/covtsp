"""Generate route animation videos for ASCII background.

Creates a short mp4 for each city showing the solved route progressively
drawing through station dots. Background has subtle noise texture so the
ASCII renderer produces varied characters instead of uniform output.

Output: web/route-{city}.mp4 (640px wide, ~16s each)
"""

import json
import subprocess
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

WEB = Path(__file__).resolve().parent.parent / 'web'

CITIES = {
    'london': WEB / 'sample.json',
    'nyc':    WEB / 'nyc.json',
    'berlin': WEB / 'berlin.json',
}

# Video params
WIDTH_PX = 640
FPS = 24
DURATION_S = 16
TOTAL_FRAMES = FPS * DURATION_S

# Style
BG_BASE = 0.85  # base brightness (0-1), light gray
BG_NOISE_AMP = 0.15  # amplitude of noise texture on background
STATION_COLOR = '#444444'
STATION_ALPHA_UNVISITED = 0.3
STATION_ALPHA_VISITED = 0.95
ROUTE_COLOR = '#222222'
ROUTE_ALPHA = 0.85
STATION_SIZE_UNVISITED = 2.0
STATION_SIZE_VISITED = 5.0
HEAD_SIZE = 14.0
HEAD_COLOR = '#000000'
ROUTE_WIDTH = 0.8

CITY_LABELS = {
    'london': 'LONDON',
    'nyc': 'NEW YORK',
    'berlin': 'BERLIN',
}


def load_city(path):
    with open(path) as f:
        data = json.load(f)

    stations = data['stations']
    route = data['route']

    all_lats = [s['lat'] for s in stations.values()]
    all_lons = [s['lon'] for s in stations.values()]
    route_lats = [r['lat'] for r in route]
    route_lons = [r['lon'] for r in route]

    return {
        'all_lats': all_lats,
        'all_lons': all_lons,
        'route_lats': route_lats,
        'route_lons': route_lons,
        'name': data.get('city', ''),
        'n_stations': len(stations),
        'n_route': len(route),
    }


def make_noise_bg(width_px, height_px, seed):
    """Generate a noisy background image with varied brightness.

    Uses multi-scale noise to create organic-looking texture that maps
    to varied ASCII characters instead of uniform output.
    """
    rng = np.random.RandomState(seed)

    # Start with base brightness
    bg = np.full((height_px, width_px), BG_BASE)

    # Layer 1: large-scale smooth variation (gradient-like)
    # Create smooth blobs using downsampled noise + bicubic upscale
    from scipy.ndimage import gaussian_filter
    coarse = rng.randn(height_px // 16 + 1, width_px // 16 + 1) * 0.08
    # Upsample
    from scipy.ndimage import zoom
    coarse_up = zoom(coarse, (height_px / coarse.shape[0], width_px / coarse.shape[1]), order=3)
    coarse_up = coarse_up[:height_px, :width_px]
    bg += coarse_up

    # Layer 2: medium-scale noise
    med = rng.randn(height_px // 4 + 1, width_px // 4 + 1) * 0.05
    med_up = zoom(med, (height_px / med.shape[0], width_px / med.shape[1]), order=2)
    med_up = med_up[:height_px, :width_px]
    bg += med_up

    # Layer 3: fine grain
    fine = rng.randn(height_px, width_px) * 0.03
    bg += fine

    # Clamp
    bg = np.clip(bg, 0.5, 1.0)

    return bg


def render_frame(city, city_key, frame_idx, total_frames, fig, ax, noise_bg):
    """Render a single frame of the route animation."""
    ax.clear()
    ax.set_facecolor('black')  # will be covered by noise image
    fig.set_facecolor('black')
    ax.set_aspect('equal')
    ax.axis('off')

    # Bounds
    lon_min = min(city['all_lons'])
    lon_max = max(city['all_lons'])
    lat_min = min(city['all_lats'])
    lat_max = max(city['all_lats'])
    pad_x = (lon_max - lon_min) * 0.12
    pad_y = (lat_max - lat_min) * 0.12
    ax.set_xlim(lon_min - pad_x, lon_max + pad_x)
    ax.set_ylim(lat_min - pad_y, lat_max + pad_y)

    # Draw noise background as image
    ax.imshow(
        noise_bg, extent=[lon_min - pad_x, lon_max + pad_x, lat_min - pad_y, lat_max + pad_y],
        aspect='auto', cmap='gray', vmin=0, vmax=1, zorder=0,
    )

    # Progress
    progress = frame_idx / max(total_frames - 1, 1)
    route_idx = int(progress * (city['n_route'] - 1))

    # All stations (dim)
    ax.scatter(
        city['all_lons'], city['all_lats'],
        s=STATION_SIZE_UNVISITED, c=STATION_COLOR,
        alpha=STATION_ALPHA_UNVISITED, zorder=1, linewidths=0,
    )

    # City label
    label = CITY_LABELS.get(city_key, city_key.upper())
    ax.text(
        0.05, 0.95, label,
        transform=ax.transAxes,
        fontsize=11, fontweight='bold',
        color='#333333', alpha=0.45,
        va='top', ha='left',
        fontfamily='monospace',
    )

    if route_idx < 1:
        ax.scatter(
            [city['route_lons'][0]], [city['route_lats'][0]],
            s=HEAD_SIZE, c=HEAD_COLOR, alpha=1.0, zorder=5, linewidths=0,
        )
        return

    r_lons = city['route_lons'][:route_idx + 1]
    r_lats = city['route_lats'][:route_idx + 1]

    # Route line (thin)
    ax.plot(
        r_lons, r_lats,
        color=ROUTE_COLOR, alpha=ROUTE_ALPHA,
        linewidth=ROUTE_WIDTH, zorder=2, solid_capstyle='round',
    )

    # Visited stations
    ax.scatter(
        r_lons, r_lats,
        s=STATION_SIZE_VISITED, c=STATION_COLOR,
        alpha=STATION_ALPHA_VISITED, zorder=3, linewidths=0,
    )

    # Current position head
    ax.scatter(
        [r_lons[-1]], [r_lats[-1]],
        s=HEAD_SIZE, c=HEAD_COLOR, alpha=1.0, zorder=5, linewidths=0,
    )


def generate_city_video(city_key, city_path, output_path):
    """Generate an mp4 for one city's route animation."""
    print(f"  Generating {city_key}...")
    city = load_city(city_path)

    # Figure size
    lon_range = max(city['all_lons']) - min(city['all_lons'])
    lat_range = max(city['all_lats']) - min(city['all_lats'])
    aspect = lat_range / lon_range if lon_range > 0 else 1.0
    height_px = round(WIDTH_PX * aspect)
    if height_px % 2 == 1:
        height_px += 1
    fig_w = WIDTH_PX / 100
    fig_h = height_px / 100

    # Pre-generate noise backgrounds (slowly evolving)
    # Use a few keyframes and interpolate for subtle movement
    n_key = 5
    key_seeds = list(range(42, 42 + n_key))
    noise_keys = [make_noise_bg(WIDTH_PX, height_px, s) for s in key_seeds]

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=100)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(TOTAL_FRAMES):
            # Interpolate between noise keyframes for subtle evolution
            t = i / max(TOTAL_FRAMES - 1, 1) * (n_key - 1)
            k0 = int(t)
            k1 = min(k0 + 1, n_key - 1)
            frac = t - k0
            noise_bg = noise_keys[k0] * (1 - frac) + noise_keys[k1] * frac

            render_frame(city, city_key, i, TOTAL_FRAMES, fig, ax, noise_bg)
            fig.savefig(
                f"{tmpdir}/frame_{i:04d}.png",
                dpi=100, facecolor='black', pad_inches=0,
            )
            if (i + 1) % 96 == 0:
                print(f"    frame {i + 1}/{TOTAL_FRAMES}")

        plt.close(fig)

        subprocess.run([
            'ffmpeg', '-y',
            '-framerate', str(FPS),
            '-i', f'{tmpdir}/frame_%04d.png',
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-c:v', 'libx264',
            '-crf', '26',
            '-preset', 'slow',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-an',
            str(output_path),
        ], check=True, capture_output=True)

    size_kb = output_path.stat().st_size / 1024
    print(f"    → {output_path.name} ({size_kb:.0f} KB)")


def main():
    print("Generating route animation videos...")
    for city_key, city_path in CITIES.items():
        if not city_path.exists():
            print(f"  Skipping {city_key} — {city_path} not found")
            continue
        output = WEB / f'route-{city_key}.mp4'
        generate_city_video(city_key, city_path, output)

    print("Done!")


if __name__ == '__main__':
    main()
