# Synthetic short film

`movie.mkv.gz` is a deterministic compressed fixture, not downloaded media. It expands to a valid one-minute 1920×1080 Matroska video with four black FFV1 frames, silent FLAC audio and a reserved index region. The size is above the unchanged production 1080p minimum for a one-minute film. `movie.json` records its exact size, SHA256 and media properties. The harness verifies the base fixture, writes a run/case identifier into 128 bytes of the reserved Matroska Void starting at offset 1024, and generates torrent hashes from the resulting actual bytes. Each case gets a unique filename, subtitle, torrent hash and media checksum. The Void contains arbitrary padding by design, so duration, dimensions, codec data, byte length and seek positions remain valid. The real downloader transfers and verifies each distinct payload.

The compressed fixture is small because its video, silence and padding compress well. Compression is only for repository storage; the torrent transfers the full uncompressed payload. Arr uses the actual ffprobe included in its pinned release artifact.

FFmpeg was used once to author the fixture. It is neither a production nor CI dependency. Regeneration is optional and requires reviewing/updating `movie.json`:

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i color=black:s=1920x1080:r=1/15 \
  -f lavfi -i anullsrc=r=8000:cl=mono -t 60 -c:v ffv1 -c:a flac \
  -reserve_index_space 16777216 synthetic-movie.mkv
```
