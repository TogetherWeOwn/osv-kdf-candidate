# osv-kdf-candidate

Offline KDF candidate verification for Open Serverless Vault (synthetic fixtures only).

## Status

This repository currently contains only a CI scaffold. There is no KDF
implementation, cryptographic test vector, WASM artifact, or performance result.
A successful `ci-ok` check validates the scaffold, not a cryptographic candidate.

## Scaffold checks

Run the dependency-free guard tests with Python 3:

```sh
python3 -I -m unittest discover -s .github/tests -v
```

CI also runs checksum-pinned actionlint, a checksum-pinned full-history gitleaks
scan with redaction, PR text validation, and advisory PR-size labels. The `ci-ok`
aggregator fails if scaffold validation fails, is cancelled, is skipped, or has
missing evidence. Secret scanning reports separately as `gitleaks`.

No workflow deploys, releases, or publishes an artifact. No workflow uses repository
secrets. Candidate implementation and its verification are separate work.

## Contributing

Follow the organization [contribution contract](https://github.com/TogetherWeOwn/.github/blob/main/CONTRIBUTING.md)
and [pipeline standard](https://github.com/TogetherWeOwn/.github/blob/main/PIPELINE-STANDARD.md).
