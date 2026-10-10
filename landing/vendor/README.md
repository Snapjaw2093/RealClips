# Third-party code (copied, not modified)

Used by the in-browser video compressor at `/compress/`.

| Folder | Package | Version | License |
|---|---|---|---|
| `ffmpeg/` | [@ffmpeg/ffmpeg](https://github.com/ffmpegwasm/ffmpeg.wasm) | 0.12.10 | MIT |
| `util/`   | [@ffmpeg/util](https://github.com/ffmpegwasm/ffmpeg.wasm)   | 0.12.1  | MIT |

These are the small JavaScript wrappers. The FFmpeg engine itself
(`@ffmpeg/core` 0.12.6, ~30 MB WebAssembly) is NOT stored here: the page loads
it from the jsDelivr CDN the first time it's needed. FFmpeg and x264 have their
own licenses (LGPL/GPL); see https://ffmpeg.org/legal.html.

To update, install the packages with npm and copy `dist/esm/*.js` into these folders.
