# Local Whisper Transcriber

A small, private web app that turns audio or video recordings into text with
OpenAI's open-source Whisper model. Drop a file into the browser, wait for the
local model to finish, then copy or download the complete transcript.

The supplied Docker setup supports a portable CPU image and an NVIDIA CUDA
image. No API key or hosted transcription service is used.

## Features

- Drag-and-drop and file-picker uploads
- Common recording formats including MOV, MP4, M4A, MP3, WAV, WebM, and MKV
- Local Whisper inference with CPU fallback
- Persistent model cache and a host-visible `transcripts` directory
- Localhost-only port binding
- Windows launch and stop shortcuts
- Non-root, read-only containers with reduced Linux privileges

## Quick start on Windows

Prerequisites:

- Docker Desktop running with Linux containers
- Enough free disk space for the image and selected Whisper model
- Optional: an NVIDIA GPU available to Docker Desktop through WSL2

Then double-click `Start Transcriber.bat`. The launcher will:

1. verify that Docker Desktop is ready;
2. build and probe the CUDA image when GPU mode is available;
3. fall back to the CPU image if that probe fails;
4. wait for the server health check; and
5. open the local page in the default browser.

The default address is <http://127.0.0.1:43127>. If that port is occupied, the
launcher chooses a free nearby port for that run. Finished text files appear in
the `transcripts` folder. Double-click `Stop Transcriber.bat` to stop the app;
transcripts and cached models are preserved.

You can force a mode from Command Prompt or PowerShell:

```powershell
& '.\Start Transcriber.bat' cpu
& '.\Start Transcriber.bat' gpu
```

The first build can take several minutes. The first transcription with a given
model also downloads its weights into the `whisper-models` Docker volume.

## Configuration

Copy `.env.example` to `.env` and edit values only when needed:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBER_PORT` | `43127` | Localhost port opened in the browser |
| `WHISPER_MODEL` | `small.en` | Whisper model name |
| `MAX_UPLOAD_BYTES` | `4294967296` | Maximum upload size in bytes (4 GiB) |
| `PYTHON_IMAGE` | `python:3.11.16-slim-bookworm` | Container base image |
| `TORCH_VERSION` | `2.11.0` | CPU/CUDA PyTorch wheel version |

English-only model names ending in `.en` are usually a good fit for English
recordings. Larger models can improve accuracy but require more memory, storage,
and processing time.

Multipart uploads are temporarily spooled to the Docker work volume. For a
4 GiB recording, keep at least 8 GiB of free disk space plus room for audio
decoding and model files.

## Compose commands

The CPU and GPU services are separate Compose profiles and share one host port,
so run only one profile at a time.

```powershell
docker compose --profile cpu up --build -d transcriber-cpu
docker compose --profile gpu up --build -d transcriber-gpu
docker compose --profile cpu --profile gpu down
```

To remove downloaded model and temporary-work volumes as well as containers,
run the following deliberate cleanup command. Files already written to the host
`transcripts` directory are not removed.

```powershell
docker compose --profile cpu --profile gpu down --volumes
```

## Development and validation

Validate the Compose file and run focused tests before publishing changes:

```powershell
docker compose --profile cpu --profile gpu config --quiet
python -m pip install --requirement requirements-dev.txt
python -m pytest -q
docker build --target runtime-cpu --tag transcriptions-automation:cpu .
```

GPU validation requires Docker Desktop NVIDIA support:

```powershell
docker build --target runtime-gpu --tag transcriptions-automation:gpu .
docker run --rm --gpus all --entrypoint python transcriptions-automation:gpu `
  -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
```

The application uses one Gunicorn worker because job state and the transcription
queue are process-local. Threads keep health and status requests responsive
while the single transcription worker serializes model-heavy jobs.

## Privacy and publishing

Recordings, transcripts, model weights, and caches are intentionally absent
from the image build context and ignored by Git. Spoken content is untrusted
data to transcribe; it is never interpreted as an instruction to the app.

Read [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before exposing a modified
deployment or publishing prebuilt images.

## License

Project source is available under the [MIT License](LICENSE). Third-party
components and model assets retain their respective licenses. The software is
provided **as is**, without warranty of any kind, as stated in the license.
