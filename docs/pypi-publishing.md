# Publishing the Python package

The workflow `.github/workflows/pypi-publish.yml` uses PyPI Trusted Publishing.
The PyPI publisher must match repository `Machboost/Machboost`, workflow
`pypi-publish.yml`, and GitHub environment `pypi`. No permanent PyPI API token
is stored in GitHub. Account email verification and two-factor authentication
are configured directly in PyPI.

## Release procedure

1. Update the package, native app, and runtime manifest versions together.
2. Merge through the protected main branch and wait for CI to pass.
3. Create and push the matching stable `vMAJOR.MINOR.PATCH` tag.
4. Review and approve the `pypi` environment deployment in GitHub Actions.
5. Confirm that the public-index verification job succeeds before announcing it.

For recovery, manually dispatch **Python Release** from `main` with an existing
version such as `0.16.20`. The workflow checks out that exact tag, requires it to
be reachable from main, and checks for successful main CI on the same commit.
PyPI does not allow replacing published files; never retag or overwrite a release.
If an upload partially succeeds, inspect PyPI before retrying; the workflow does
not silently skip existing files.

## Safeguards

Pull requests build and check the distributions but cannot publish. The build
job has no publishing identity. Only the isolated publish job receives
`id-token: write`, after environment approval. Restrict the `pypi` environment
to `main` and version tags, with a maintainer as required reviewer.

The workflow checks wheel and source metadata, rejects local/runtime artifacts,
and installs both archives in fresh environments outside the source checkout.
After publishing, it installs from the public PyPI index and runs the CLI smoke
test. These checks exercise package delivery, not GPU inference quality.

The Python package contains no downloaded weights or bundled macOS runtime.
`machboost[mlx]` installs the optional MLX dependencies on compatible Macs;
plain `machboost` installs the base client/server package. The DMG is distributed
separately through GitHub Releases.

Native dependency ranges intentionally stay within MLX 0.32, MLX-LM 0.31, and
MLX-VLM 0.6 for this release. Validate model loading, streaming, tool calls, and
cache behavior before widening those minor-version ranges. The desktop runtime
uses its separate checked-in dependency lock.

[PyPI Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
