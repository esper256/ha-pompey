# World Trigger catalogue

`world_trigger.json` normalizes the Prowlarr UI copy supplied on 2026-09-14: 203 rows, seven sources. It stores release labels and displayed metadata only, with no torrent files, source URLs or real hashes. The original paste is not required to run tests.

To import another complete copy:

```sh
python3 tools/import_prowlarr_results.py input.txt output.json
```

Blank lines and the optional Grabs column are accepted. Incomplete rows fail loudly. Ages are retained as days; the HTTP fixture emits relative publication dates. Magnets have generated hashes and a loopback-only tracker. Nothing launches a torrent client.

The real-Sonarr tests use two identical local mirrors to expose duplicate-result handling and fan-out without relying on public indexers. All rows are exposed as anime-category candidates, including incorrectly categorized or unrelated material, so Sonarr must reject it. Query routing uses season/episode hints derived from labels plus real series metadata. This models a catalogue, not the quirks or uptime of seven scraper implementations.

Controlled scenarios use captured episode titles and the captured 4.9 GiB S1PH3R x265 WEBRip season-three pack. A synthetic single-audio version keeps the same codec, size and quality. Phantom availability (zero seeds, two leechers) and unavailable episode variants are explicit scenario alterations; the stored capture stays unchanged. The separate complete-catalogue search uses every row as captured. Generated ordinary-TV releases test the shared profile's rejection rules, and a synthetic 2160p pack tests Max's upgrade behavior.
