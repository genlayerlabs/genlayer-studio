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

See `docs/BRANCHING.md` for the contributor-facing branch model.
