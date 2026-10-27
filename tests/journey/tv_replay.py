"""Synthetic series in observed Skyhook and TMDB response schemas."""

import json
from pathlib import Path


def skyhook(case):
    data = json.loads(Path("/tests/journey/replay/sonarr-series.json").read_text())
    data.update(
        tvdbId=case.tvdb_id,
        tmdbId=case.movie_id,
        title=case.title,
        slug=case.title.lower(),
        imdbId="tt" + case.imdb_id,
        originalLanguage="jpn" if case.anime else "eng",
        contentRating=case.certification,
        genres=["Anime"] if case.anime else [],
        seasons=[{"seasonNumber": s, "images": []} for s in case.seasons],
    )
    episode = data["episodes"][0]
    data["episodes"] = [
        dict(
            episode,
            tvdbShowId=case.tvdb_id,
            tvdbId=case.tvdb_id * 100 + s * 10 + n,
            seasonNumber=s,
            episodeNumber=n,
            title=f"Episode {s}-{n}",
        )
        for s in case.seasons
        for n in range(1, case.episode_count + 1)
    ]
    return data


def tv(case):
    return {
        "id": case.movie_id,
        "name": case.title,
        "original_name": case.title,
        "original_language": "ja" if case.anime else "en",
        "overview": "Synthetic one-minute episodes for acceptance.",
        "first_air_date": "2024-01-01",
        "last_air_date": "2024-01-02",
        "status": "Ended",
        "type": "Scripted",
        "adult": False,
        "in_production": False,
        "episode_run_time": [1],
        "number_of_episodes": case.episode_count * len(case.seasons),
        "number_of_seasons": len(case.seasons),
        "origin_country": ["JP" if case.anime else "US"],
        "genres": [{"id": 16, "name": "Animation"}] if case.anime else [],
        "languages": ["ja" if case.anime else "en"],
        "networks": [],
        "production_companies": [],
        "production_countries": [],
        "spoken_languages": [],
        "poster_path": None,
        "backdrop_path": None,
        "popularity": 1,
        "vote_average": 7,
        "vote_count": 1,
        "created_by": [],
        "homepage": "",
        "tagline": "",
        "credits": {"cast": [], "crew": []},
        "aggregate_credits": {"cast": [], "crew": []},
        "videos": {"results": []},
        "keywords": {
            "results": [{"id": 210024, "name": "anime"}] if case.anime else []
        },
        "external_ids": {"imdb_id": "tt" + case.imdb_id, "tvdb_id": case.tvdb_id},
        "watch/providers": {"results": {}},
        "content_ratings": {
            "results": [{"iso_3166_1": "US", "rating": case.certification}]
        },
        "seasons": [
            dict(season(case, s), episode_count=case.episode_count)
            for s in case.seasons
        ],
    }


def season(case, number):
    return {
        "id": case.tvdb_id * 10 + number,
        "season_number": number,
        "name": f"Season {number}",
        "overview": "Synthetic season.",
        "air_date": "2024-01-01",
        "poster_path": None,
        "external_ids": {"tvdb_id": case.tvdb_id * 10 + number},
        "episodes": [
            {
                "id": case.tvdb_id * 100 + number * 10 + n,
                "season_number": number,
                "episode_number": n,
                "name": f"Episode {number}-{n}",
                "overview": "Synthetic episode.",
                "air_date": "2024-01-01",
                "show_id": case.movie_id,
                "vote_average": 7,
                "vote_count": 1,
                "still_path": None,
                "production_code": "",
            }
            for n in range(1, case.episode_count + 1)
        ],
    }
