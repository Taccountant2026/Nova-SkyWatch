# NOVA SkyWatch

An open-source Raspberry Pi satellite tracking and observation system, part of the NOVA intelligent systems ecosystem.

## v0.2 — Orbital prediction prototype

The original animated demo is preserved as `simulation.html`. The live dashboard consumes a local Flask API backed by Skyfield SGP4 and CelesTrak GP orbital data. It shows predicted azimuth, elevation and slant range, a 24-hour list of complete passes above a configurable threshold, a polar pass path, and an opt-in audio alert for the selected target. It is not an SDR receiver or optical visibility detector.

### Installation

Python 3.10+ and internet access are required. From the repository directory:

```bash
python -m venv .venv
```

On Raspberry Pi OS / Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If activation is blocked, use the environment's Python executable directly.

```bash
python -m pip install -r requirements.txt
python server.py --lat YOUR_LATITUDE --lon YOUR_LONGITUDE
```

Replace the two placeholders with decimal coordinates for the observation site (north/east positive; west negative). These remain local and are not committed to the repository. Open http://127.0.0.1:8000 in a browser on the same device. Use the Demo mode link to view the original simulation. For the offline demo alone, open simulation.html directly.

Optional arguments: `--threshold 20` (degrees above horizon), `--group stations` (default) or `--group visual`, and `--port 8000`. Stop the server with Ctrl+C.

### Architecture and operating notes

- Dashboard: original mission-control design with a predicted polar track and current selected-target position.
- API: GET /api/status or /api/status?satellite=25544 (when ISS is in the selected catalogue).
- CelesTrak CSV/OMM cache: ~/.nova-skywatch; fetch no more than every two hours per group from a single running instance. Failed requests are backed off; cached data may be used with an explicit warning.
- Elements older than seven days are excluded. Fresh elements still do not guarantee accurate positions; especially after manoeuvres.
- Default listen address is loopback only; no public hosting or remote access is configured. The Flask development server is for local testing only.
- Passes use a geometric elevation threshold, not confirmed visibility or radio reception. Alerts currently follow the selected satellite while the dashboard is open and polling. Very brief passes may be missed between polls. Continuous all-target monitoring and physical SDR detection are future work.
- The development prototype needs live network and Raspberry Pi acceptance testing.

### Roadmap

- v0.1: simulated radar interface
- v0.2: local orbital API and live dashboard
- v0.3: continuous multi-satellite alerts, persistent event log and location settings
- v0.4: Raspberry Pi kiosk and service deployment
- v0.5: SDR receiver experiments
- v0.6: Nova OS integration

Data: https://celestrak.org/NORAD/documentation/gp-data-formats.php
Orbital library: https://rhodesmill.org/skyfield/earth-satellites.html

MIT License — see LICENSE.
