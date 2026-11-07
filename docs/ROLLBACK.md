# Rollback

Last good commit: see `git log`; every commit on `main` has been deployed and run on the device. Rebuild after any code rollback with `make all` on the Nano and restart the services.

## Automatic

`deploy/deploy.sh` keeps the previous files in `<app_dir>/.prev/` and restores them, then restarts the services, if the new version is not running and reporting itself in `status.json` within 60 s. `.prev` holds what was installed, so since v1.13.0 that is `webapp.so` and the two-line `web.py`, not source (D-043).

## Code

1. `git revert <commit>` (or `git checkout <commit> -- <file>`), push.
2. On the Nano: `sh deploy/deploy.sh vX.Y.Z <app_dir>` for a release, or copy the files into the app folder, `make all`, `sh deploy/compile-web.sh` (the device runs `webapp.so`, not `web.py`, D-043), `sudo systemctl restart facepipe facepipe-web`.

## Services

| Change | How to undo |
| --- | --- |
| Autostart (installed by `deploy/install.sh`) | `sudo systemctl disable --now facepipe facepipe-web`, then `sudo rm /etc/systemd/system/facepipe*.service && sudo systemctl daemon-reload` |
| Camera or settings | Edit `<app_dir>/facepipe.env`, `sudo systemctl restart facepipe` |
| Gallery | Remove or restore `gallery/<name>/`, run `./facepipe-enroll gallery`; the service reloads it |

## Device configuration

| Change | How to undo |
| --- | --- |
| IMX477 camera overlay | `sudo cp /boot/extlinux/extlinux.conf.bak-imx477 /boot/extlinux/extlinux.conf && sudo reboot` |
| Recordings volume | `sudo umount /mnt/rec`, delete the `/var/lib/facepipe-rec.img` line from `/etc/fstab`, `sudo rm /var/lib/facepipe-rec.img` (deletes all clips) |
| SSD removed from fstab | `/etc/fstab.bak-ssd-removed` holds the old lines; only useful if the `nano-data` partition still exists |

## Data

Attendance events, snapshots and the gallery are files under `<app_dir>/data`; back them up by copying the folder. There is no migration step to undo.

## CSI camera gives no frames after a stop or crash

If the facepipe log shows `end of stream` right after start, or `watchdog: no frames`, and `journalctl -u nvargus-daemon` shows `SCF: Error Timeout` or `Error InvalidState`, the camera daemon is wedged: `sudo systemctl stop facepipe && sudo systemctl restart nvargus-daemon && sudo systemctl start facepipe`.
