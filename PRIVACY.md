# Privacy

This application is designed for local transcription.

- The web server is published only on `127.0.0.1`, so it is not exposed to the
  local network by the supplied Compose configuration.
- Uploaded media is copied into a Docker work volume while it is processed and
  is deleted by the application after the job succeeds or fails.
- Completed `.txt` files are written to the local `transcripts` directory.
- The application does not send recordings or transcript text to an API and
  does not include analytics or telemetry.
- Whisper model weights are downloaded from OpenAI's public model storage the
  first time a model is used. Docker images and Python packages also require a
  network connection during initial setup. Once those assets are cached, local
  transcription does not require a cloud transcription service.

The `transcripts` directory, common media formats, model weights, caches, and
local `.env` configuration are excluded from Git. Review staged files before
publishing a fork because ignore rules cannot remove files that were already
committed.

Anyone deploying a modified version on a network is responsible for adding
authentication, TLS, retention controls, and any consent notices required by
their jurisdiction. Only transcribe recordings you are authorized to process.
