# Synthetic short film

`movie.mkv.gz` is a deterministic compressed fixture, not downloaded media. It expands to a valid one-minute 1920×1080 Matroska video with four black FFV1 frames, silent FLAC audio and a reserved index region. The size is above the unchanged production 1080p minimum for a one-minute film. `movie.json` records its exact size, SHA256 and media properties. The harness verifies the base fixture, writes a run/case identifier into 128 bytes of the reserved Matroska Void starting at offset 1024, and generates torrent hashes from the resulting actual bytes. Each case gets a unique filename, subtitle, torrent hash and media checksum. The Void contains arbitrary padding by design, so duration, dimensions, codec data, byte length and seek positions remain valid. The real downloader transfers and verifies each distinct payload.

Generated source files represent the long zero-filled Void as a sparse hole. The peer streams exact logical ranges across the media/subtitle files instead of keeping a full movie payload per case in memory.

The compressed fixture is small because its video, silence and padding compress well. Compression is only for repository storage; the torrent transfers the full uncompressed payload. Arr uses the actual ffprobe included in its pinned release artifact.

FFmpeg was used once to author the fixture. It is neither a production nor CI dependency. Regeneration is optional and requires reviewing/updating `movie.json`:

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i color=black:s=1920x1080:r=1/15 \
  -f lavfi -i anullsrc=r=8000:cl=mono -t 60 -c:v ffv1 -c:a flac \
  -reserve_index_space 16777216 synthetic-movie.mkv
```

`movie-480p.mkv.gz` is a second valid one-minute fixture at 854×480 with the same reserved-void technique. It lets the Default/Anything acceptance checks distinguish genuinely lower-resolution media. Its JSON records its own exact hash, size and padding boundaries. It was authored once with the host's FFmpeg using `color=black:s=854x480:r=1/15`, silent 8 kHz mono audio, FFV1/FLAC and 16 MiB of reserved index space. Neither runtime nor CI installs FFmpeg.

TV offers contain one or two individually named one-minute episode files and matching subtitles. Every offered alternative has its own torrent name, hash and payload identity. A pack's reported size is the sum of its actual files; no advertised multi-gigabyte sizes are substituted.

The `tv-*.mkv.gz` fixtures contain real silent FLAC tracks tagged `eng`, `jpn`, `deu`, `jpn+eng`, or `eng+deu`. Dual-audio offers contain two tracks, rather than simply claiming dual audio in their names. Native Sonarr media probing asserts the imported stream count. All variants use the same one-minute 1080p FFV1/padding recipe and have separately verified manifests. The identity region combines the case prefix with a digest of the offer and episode filename, so every episode and alternative has distinct bytes.

The primary baseline movie torrent additionally contains namespaced `.nfo`, `.txt` and `.url` files generated as small synthetic text fixtures. They participate in actual torrent lengths, piece hashes and downloads. Shared cleanup asserts their absence across household media storage after import, while preserving the movie and subtitle.
