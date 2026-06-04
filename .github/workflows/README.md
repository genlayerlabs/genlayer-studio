# Studio CI Notes

`branch-policy.yml` keeps the release-train model explicit:

- feature PRs go to the active `*-dev` integration branch
- promotion PRs into a release branch must come from the matching `*-dev`
  branch, for example `v0.123-dev` into `v0.123`
- `main`, `master`, `v0.122`, `release-from-main.yml`, and
  `release.config.js` are treated as invalid release surfaces
- releases must go through version tags validated by `release-from-tag.yml`
