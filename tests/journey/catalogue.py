"""Namespaced external movie identities, including unique bytes in Matroska padding."""

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Case:
    index: int
    prefix: str
    certification: str

    @property
    def scenario(self):
        return {
            0: "movie-pg",
            1: "movie-r",
            2: "anything-480p",
            3: "late-certification",
            4: "waiting-content",
            5: "single-episode",
            6: "season-pack",
            7: "selected-seasons",
            8: "anime-dual-audio",
            9: "restart-partial",
            10: "vpn-download-loss",
            11: "vpn-rotation",
        }.get(self.index, "movie-control")

    @property
    def kind(self):
        return "tv" if self.index in {5, 6, 7, 8} else "movie"

    @property
    def tvdb_id(self):
        return 900000 + self.index

    @property
    def anime(self):
        return self.index == 8

    @property
    def episode_count(self):
        return 1 if self.index == 5 else 2

    @property
    def seasons(self):
        return [1, 2] if self.index == 7 else [1]

    @property
    def requested_seasons(self):
        return [2] if self.index == 7 else [1]

    @property
    def late_certification(self):
        return self.index == 3

    @property
    def movie_id(self):
        return 1184918 + self.index

    @property
    def imdb_id(self):
        return str(29623480 + self.index)

    @property
    def title(self):
        return self.prefix

    @property
    def release(self):
        if self.kind == "movie":
            return (
                self.prefix
                + ".2024."
                + ("480p" if self.index == 2 else "1080p")
                + ".WEB-DL"
            )
        season = self.requested_seasons[0]
        tag = f".S{season:02d}" + ("E01" if self.index == 5 else "")
        return (
            self.prefix
            + tag
            + ".1080p.WEB-DL."
            + ("JAPANESE.ENGLISH.DUAL.AUDIO" if self.anime else "ENGLISH")
        )

    @property
    def library(self):
        return (
            "Kid Friendly"
            if self.certification in {"PG", "TV-PG"}
            else "Not Kid Friendly"
        )


@dataclass(frozen=True)
class Offer:
    case: Case
    variant: int
    release: str
    season: int = 1
    episodes: tuple = ()
    quality: str = "1080p"
    language: str = "original"

    @property
    def movie_id(self):
        # Fixture token, not a metadata identity; alternatives have real unique hashes.
        return self.case.movie_id + self.variant * 10000

    @property
    def audio_languages(self):
        if self.language == "foreign":
            return ("deu",)
        if self.case.anime:
            return ("jpn", "eng") if self.variant == 0 else ("jpn",)
        if self.language == "dual":
            return ("eng", "deu")
        return ("eng",)

    @property
    def fixture_name(self):
        if self.case.kind == "tv":
            return "tv-" + "-".join(self.audio_languages)
        return "movie-480p" if self.quality == "480p" else "movie"

    @property
    def files(self):
        if self.case.kind == "movie":
            return [self.release + ".mkv"]
        return [
            f"{self.case.prefix}.S{self.season:02d}E{n:02d}.{self.quality}.WEB-DL.mkv"
            for n in self.episodes
        ]


def offers(case):
    if case.kind == "movie":
        rows = [
            Offer(case, 0, case.release, quality="480p" if case.index == 2 else "1080p")
        ]
        if case.index == 0:
            rows.append(
                Offer(case, 1, case.prefix + ".2024.480p.WEB-DL", quality="480p")
            )
        return rows
    season = case.requested_seasons[0]
    episodes = tuple(range(1, case.episode_count + 1))
    rows = [Offer(case, 0, case.release, season, episodes)]
    if case.index == 5:
        rows.extend(
            [
                Offer(
                    case,
                    1,
                    case.prefix + ".S01E01.1080p.WEB-DL.GERMAN",
                    1,
                    (1,),
                    language="foreign",
                ),
                Offer(
                    case,
                    2,
                    case.prefix + ".S01E01.1080p.WEB-DL.ENGLISH.GERMAN.DUAL.AUDIO",
                    1,
                    (1,),
                    language="dual",
                ),
            ]
        )
    if case.index == 7:
        rows.append(
            Offer(case, 1, case.prefix + ".S01.1080p.WEB-DL.ENGLISH", 1, episodes)
        )
    if case.anime:
        rows.extend(
            [
                Offer(case, 1, case.prefix + ".S01.1080p.WEB-DL.JAPANESE", 1, episodes),
                Offer(case, 2, case.prefix + ".S01E01.1080p.WEB-DL.JAPANESE", 1, (1,)),
                Offer(
                    case,
                    3,
                    case.prefix + ".S01.1080p.WEB-DL.GERMAN",
                    1,
                    episodes,
                    language="foreign",
                ),
            ]
        )
    return rows


def cases():
    run = os.environ["POMPEY_TEST_RUN_ID"]
    if not re.fullmatch("[a-f0-9]{10}", run):
        raise ValueError("Invalid test run identity")
    count = int(os.environ.get("POMPEY_TEST_CASES", "12"))
    if not 1 <= count <= 100:
        raise ValueError("Choose between 1 and 100 scenarios")
    ratings = {3: "PG", 5: "TV-PG", 6: "TV-MA", 7: "TV-PG", 8: "TV-PG"}
    return [
        Case(i, f"Pompey{run}Case{i:03d}", ratings.get(i, ["PG", "R", ""][i % 3]))
        for i in range(count)
    ]


def prepare_media(folder):
    fixture = Path("/tests/journey/fixtures")
    templates = {}
    for case in cases():
        for offer in offers(case):
            fixture_name = offer.fixture_name
            if fixture_name not in templates:
                original = gzip.decompress(
                    (fixture / (fixture_name + ".mkv.gz")).read_bytes()
                )
                metadata = json.loads((fixture / (fixture_name + ".json")).read_text())
                if (
                    len(original) != metadata["bytes"]
                    or hashlib.sha256(original).hexdigest() != metadata["sha256"]
                ):
                    raise RuntimeError("Synthetic media fixture is corrupted")
                templates[fixture_name] = (original, metadata)
            original, metadata = templates[fixture_name]
            offset = metadata["identityPaddingOffset"]
            end = metadata["sparsePaddingEnd"]
            length = 128
            if any(original[offset:end]):
                raise RuntimeError("Identity must remain inside Matroska Void")
            target = folder / offer.release
            target.mkdir()
            for name in offer.files:
                with (target / name).open("wb") as output:
                    output.write(original[:offset])
                    output.write(
                        (
                            case.prefix
                            + ":"
                            + hashlib.sha256(
                                (offer.release + "/" + name).encode()
                            ).hexdigest()
                        )
                        .encode()
                        .ljust(length, b"\0")
                    )
                    output.seek(end)
                    output.write(original[end:])
                (target / Path(name).with_suffix(".en.srt")).write_text(
                    f"1\n00:00:00,000 --> 00:00:02,000\nSynthetic {case.prefix} {name}\n"
                )
