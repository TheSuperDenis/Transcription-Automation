# Security Policy

## Supported versions

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose local
recordings, transcripts, or host files. Use GitHub's private vulnerability
reporting for this repository and include the affected version, reproduction
steps, impact, and any suggested mitigation. Allow a reasonable period for
investigation before disclosure. If private reporting is not enabled, open a
minimal issue asking the maintainer to enable it without including sensitive
details.

## Deployment assumptions

The supplied configuration is a single-user, localhost application. It is not
designed to be exposed directly to a LAN or the public internet. Containers run
as a non-root user with a read-only root filesystem, all Linux capabilities
dropped, and `no-new-privileges` enabled. Only the transcripts bind mount and
the model/work volumes are writable.

Uploaded media must be treated as untrusted input. Keep Docker Desktop, the
base image, FFmpeg, PyTorch, Flask, Gunicorn, and Whisper updated. Do not weaken
the container restrictions or add arbitrary host-directory mounts without a
specific need and review.
