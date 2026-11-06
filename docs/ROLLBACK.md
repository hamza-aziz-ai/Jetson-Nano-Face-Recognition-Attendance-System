# Rollback

Last good commit: see `git log`; every commit on `main` has been deployed and run on the device. Rebuild after any code rollback with `make all` on the Nano and restart the services.

## Code

1. `git revert <commit>` (or `git checkout <commit> -- <file>`), push.
2. On the Nano: copy the files to `/home/hamza/facepipe`, `make all`, `sudo systemctl restart facepipe facepipe-web`.

## Services

| Change | How to undo |
| --- | --- |
| Autostart | `sudo systemctl disable --now facepipe facepipe-web`, then `sudo rm /etc/systemd/system/facepipe*.service && sudo systemctl daemon-reload` |
| Camera or settings | Edit `/home/hamza/facepipe/facepipe.env`, `sudo systemctl restart facepipe` |
| Gallery | Remove or restore `gallery/<name>/`, run `./facepipe-enroll gallery`; the service reloads it |

## Device configuration

| Change | How to undo |
| --- | --- |
| IMX477 camera overlay | `sudo cp /boot/extlinux/extlinux.conf.bak-imx477 /boot/extlinux/extlinux.conf && sudo reboot` |
| Recordings volume | `sudo umount /mnt/rec`, delete the `/var/lib/facepipe-rec.img` line from `/etc/fstab`, `sudo rm /var/lib/facepipe-rec.img` (deletes all clips) |
| SSD removed from fstab | `/etc/fstab.bak-ssd-removed` holds the old lines; only useful if the `nano-data` partition still exists |

## Data

Attendance events, snapshots and the gallery are files under `/home/hamza/facepipe/data`; back them up by copying the folder. There is no migration step to undo.
