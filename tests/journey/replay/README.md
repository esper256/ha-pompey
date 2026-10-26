# External metadata contract

`radarr-movie.json` starts from the public `https://api.radarr.video/v1/movie/1184918` response inspected during development on 2026-10-26. Its title, overview, runtime and image list were replaced for the synthetic short film. It contains no account credentials. The ID is an existing metadata identity; the fixture does not publish or modify a public catalogue entry.

`external.py` serves that Radarr response and explicit synthetic TMDB/Plex responses with matching IDs, duration and certification. These are external service contracts, not fake Arr or Seerr APIs. The isolated namespace prevents falling back to live metadata. Every request is recorded; unknown host/path combinations fail acceptance. Known update and telemetry endpoints return an explicit unavailable response.

Replay makes CI deterministic and catches changed client requests and parsing behavior. It does not prove that a live metadata provider still accepts those requests or that a real Plex server discovers/plays the imported file. Review fixture changes against the upstream contract rather than making unexpected requests return blanket successes.
