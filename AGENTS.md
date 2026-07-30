# OpenSLT Agent Instructions

## Versioning and release notes

- `VERSION` is the only source of the OpenSLT application version. Use a stable semantic
  version in `MAJOR.MINOR.PATCH` form without a `v` prefix. Do not hard-code a separate
  application version in Python, the frontend, or deployment scripts.
- Record user-visible and operations-relevant changes in the `unreleased` array in
  `RELEASES.json`. Pure test changes, formatting, and internal refactors do not need a release
  note unless they affect deployment or operators.
- Use `added`, `changed`, `fixed`, `removed`, or `security` for each change entry. Keep the text
  short and user-facing.
- For a release, choose the semantic version according to compatibility: increment `MAJOR` for
  incompatible changes, `MINOR` for backward-compatible features, and `PATCH` for compatible
  fixes. Move all unreleased entries into a new release at the start of `releases`, add its
  `YYYY-MM-DD` date and title, clear `unreleased`, and update `VERSION` in the same change.
- Run `python tools/release_metadata.py` after editing version metadata. A release is invalid if
  the newest release does not match `VERSION`, versions repeat or are out of order, or a record
  is incomplete.
- Before producing an offline package, run the backend tests, frontend tests, and frontend
  production build. The wheel version, frontend version, bundle filename, bundle `VERSION`, and
  installed bundle version must all match the repository `VERSION`.
