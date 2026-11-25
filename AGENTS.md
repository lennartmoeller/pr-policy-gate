# Instructions for Pull Request Policy Gate

- Use only the Python 3.12 standard library in `src/`; third-party packages belong to the `dev` dependency group in [`pyproject.toml`](./pyproject.toml).
- Change reader-visible output, the status description, the comment, and the job summary, only together with the tests that pin it literally, including the byte-for-byte comment examples in [`tests/fixtures/report/`](./tests/fixtures/report/).
