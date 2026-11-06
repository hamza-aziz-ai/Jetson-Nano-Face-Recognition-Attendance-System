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
5. Deploy the tag to the Nano (manual today; the self-hosted runner later) and confirm `facepipe --version` and the dashboard footer show the new version.

## Rollback

Releases are never edited or deleted. To go back, deploy the previous tag (`git checkout vX.Y.Z`, copy, `make all`, restart) and publish a new PATCH release that reverts the faulty change.
