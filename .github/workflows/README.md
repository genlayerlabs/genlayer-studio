# Studio CI Notes

`branch-policy.yml` keeps the release-train model explicit:

- independently releasable work may target a stable branch directly
- multi-feature or cross-repo train work goes to the active `*-dev` integration
  branch
- promotion PRs into a release branch normally come from the matching `*-dev`
  branch, for example `v0.123-dev` into `v0.123`
- `main` is treated as the static/default GitHub branch, not a release surface
- `master`, stale release branches, `release-from-main.yml`, and
  `release.config.js` are treated as invalid release surfaces
- releases must go through version tags validated by `release-from-tag.yml`

Release tags have two explicit channels:

- stable `vX.Y.Z` tags must point at the current `vX.Y` branch head, publish
  versioned and `latest` images, and enter the stable release lane
- prerelease `vX.Y.Z-<suffix>` tags must point at the current `vX.Y-dev`
  branch head, publish only versioned images, and update the preview deployment

`manual-docker-release.yml` applies the same rules when it creates a tag.

See `docs/BRANCHING.md` for the contributor-facing branch model.
