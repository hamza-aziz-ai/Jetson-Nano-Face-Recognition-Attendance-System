# Releasing

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single source of truth is the `VERSION` file; the Makefile compiles it into both binaries (`facepipe --version`, startup log, `status.json`) and the web page reads it for its footer.

| Change | Bump | Examples |
| --- | --- | --- |
| Breaking: configuration names, data file formats, folder layout, deployment steps | MAJOR | renaming a `FACEPIPE_*` variable, changing `gallery.tsv` |
| New feature, backwards compatible | MINOR | a new camera source, a new dashboard page |
| Fix only | PATCH | a crash fix, a CI fix that changes shipped files |

Docs-only and CI-only changes go into `[Unreleased]` and ship with the next release.

## Steps

1. Every change adds a line under `[Unreleased]` in `CHANGELOG.md` (Added, Changed, Fixed, Removed, Security).
2. To release: rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD`, add a fresh empty `[Unreleased]`, update the compare links at the bottom, set `VERSION` to `X.Y.Z`. Commit as `Release vX.Y.Z`.
3. Tag and push: `git tag -a vX.Y.Z -m "facepipe vX.Y.Z" && git push origin vX.Y.Z`.
4. The Release workflow reruns the full CI on the tag, checks that the tag matches `VERSION` and that `CHANGELOG.md` has the section, and publishes the GitHub Release with that section as notes.
5. The Deploy to Nano workflow (self-hosted runner on the Nano, enabled by the repository variable `NANO_RUNNER=true`) builds the tag on the device, runs `facepipe --selftest` and the web self-test, checks the version, uploads the release assets (`facepipe-X.Y.Z-jetson-nano-jetpack4.6-arm64.tar.gz` and `SHA256SUMS`), installs and restarts the services, and rolls back if `status.json` does not report the new version within 60 s. Without the runner, run `sh deploy/deploy.sh vX.Y.Z` on the Nano by hand; it does the same except the upload.

## Release assets

Each release carries what a Nano installs, built and tested on a Nano: both binaries, `webapp.so` with its two-line `web.py` starter (D-043), `VERSION`, `facepipe.env.example`, the deploy scripts, README and CHANGELOG, plus `SHA256SUMS`. Models, engines, logins, data and the gallery are never included. Verify with `sha256sum -c SHA256SUMS`. There are no Docker images: the project runs natively on the Nano (D-026).

## Rollback

Releases are never edited or deleted. To go back, deploy the previous tag (`git checkout vX.Y.Z`, copy, `make all`, restart) and publish a new PATCH release that reverts the faulty change.

## Automatic deployment (D-031)

Releases are made by `.github/workflows/release.yml` when a merged change raises `VERSION` (D-032); nobody tags by hand.

On a device installed with `deploy/install.sh`, `facepipe-update.timer` checks every 10 minutes and deploys a newer published release by itself, with rollback. So publishing the release (pushing a `vX.Y.Z` tag after green CI) is the deployment. Follow it in `logs/<year>/<Month>/update.log`, or run it at once with `sudo systemctl start facepipe-update`.
