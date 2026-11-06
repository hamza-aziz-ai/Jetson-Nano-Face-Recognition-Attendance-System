# Allow policy

What an AI tool may do on this repository and the device. Review it when the project or its risk changes.

## Safe to allow

- Reading code, docs and logs; `systemctl status`, `journalctl`.
- Building with `make` in the app folder and running test runs with `timeout`.
- Editing code and docs in the repository; committing with an intent message.

## Pause and read

- Restarting or stopping the services.
- Changing `facepipe.env`, the gallery or the models.
- Rebuilding TensorRT engines.
- Pushing to the public repository (scan for IPs and secrets first).

## Owner approval with the exact command

- Boot configuration (`/boot/extlinux`), `/etc/fstab`, systemd unit installs, shell profiles.
- Reboots and shutdowns.
- Deleting data (`data/`, `/mnt/rec`, gallery).

## Never

- Typing or reading passwords; the owner sets `web_auth` and `rtsp_auth`.
- Committing `facepipe.env`, `web_auth`, `rtsp_auth`, `data/`, `gallery/`, models or engines.
- Touching the external SSD's NTFS partition.
- Force pushing.
