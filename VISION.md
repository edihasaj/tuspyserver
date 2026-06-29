# Vision

tuspyserver is a small, correct, dependency-light implementation of the
[tus](https://tus.io) resumable upload protocol for Python ASGI apps (FastAPI /
Starlette). It should stay easy to drop into an app, faithful to the tus spec, and
cheap to maintain — not grow into a file-management product.

## In Scope

- tus protocol correctness: creation, HEAD/PATCH offset handling, expiration,
  termination, and the relevant extensions and headers.
- The upload lifecycle and pluggable storage (local filesystem today; clean
  seams for other backends).
- ASGI / FastAPI integration ergonomics, configuration, and hooks.
- Tests, docs, and routine dependency maintenance that keep it installable and
  spec-conformant.

## Out of Scope

- Turning tuspyserver into a full file manager, CDN, or storage product.
- Unrelated protocols or heavy framework lock-in.
- Large API surface expansion without a clear protocol or user need.

## Merge by Default

- **Dependency updates** (dependabot bumps) that keep CI green.
- Bug fixes and tus-conformance fixes with a clear cause and bounded risk.
- Docs, examples, type hints, and tests.
- Small, backwards-compatible ergonomics improvements.

## Needs Sign-Off

- Protocol/behavior changes or new tus extensions.
- Public API changes or new configuration surface.
- New storage backends or transport assumptions.
- Breaking changes, major version bumps, or release/packaging changes.

## Roadmap

### Short-term

- Keep dependencies current and CI green (clear the open dependabot PRs).
- Tighten tus spec conformance and fix reported header/behavior bugs.
- Trusted Publishing to PyPI; healthy release hygiene.

### Long-term

- Additional storage backends behind a stable interface.
- Broader tus extension coverage and conformance testing.
- Contribute the project to the tus org if the maintainers agree; discussion is
  tracked in [tus/tus.io#525](https://github.com/tus/tus.io/issues/525).
