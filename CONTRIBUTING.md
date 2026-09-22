# Contributing

## Branches

- Keep `main` runnable.
- Use short-lived branches such as `feat/route-matrix-client` or `feat/isochrone-engine`.
- Open a pull request for review by the other team member.

## Contracts

Public request/response shapes and Python-to-C++ messages live in `contracts/`.
Any contract change must update:

1. The relevant example JSON.
2. Python schemas and tests.
3. C++ parser/tests when the engine contract changes.
4. Frontend fixtures when the public result changes.

## Definition of done

- Tests pass locally.
- No API keys or personal data are committed.
- Errors are user-visible and do not leave the UI stuck in a loading state.
- README or docs are updated for changes that affect setup or behavior.
