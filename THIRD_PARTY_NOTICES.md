# Third-party notices

This project installs and uses third-party software. Their licenses remain
their own; this file is an overview, not a replacement for the license texts
distributed with each package or image.

- **OpenAI Whisper** — MIT License —
  <https://github.com/openai/whisper>
- **PyTorch** — BSD-style license —
  <https://github.com/pytorch/pytorch>
- **Flask** — BSD-3-Clause —
  <https://github.com/pallets/flask>
- **Gunicorn** — MIT License —
  <https://github.com/benoitc/gunicorn>
- **FFmpeg** — LGPL or GPL depending on enabled build options. The container
  installs Debian's packaged build; inspect `/usr/share/doc/ffmpeg/copyright`
  in the built image for its exact terms — <https://ffmpeg.org/legal.html>
- **Python container image and Debian packages** — each component retains its
  own license — <https://hub.docker.com/_/python>

Whisper model weights may have separate provenance and usage considerations.
Review the upstream model documentation before redistributing weights. This
project does not include model weights in source control or container images.
