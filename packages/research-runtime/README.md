# Research Runtime 1.0

Independent DSH 0.2.1-rc.5 execution package. It registers implementations, binds canonical project/run/native identities, validates actual package bytes, provides native-session and private Docker workspace primitives, bounded subprocess communication, Python queue leases and a durable executor ledger. It defines no idea, dataset, candidate, score, notebook, round or final schema.

Installed method code supplies presets/tools, preflight and side-effect-free environment checks, startup, completion rules and UI routes. `/as-research` is mounted once per composed main preset through `@onefav/research-runtime/command`. The complete AnySolvers bundle supplies the shared service/UI; another method can load `./service` and `./ui` directly without AnySolvers.

Build this package with `npm run build`; the build uses only its own sources, scripts and installed dependencies. Source development resolves DSH peers from the workspace; releases use versioned tarballs. Python helpers are under `python/research_runtime`; method adapters locate this directory from the installed package and explicitly set their child interpreter path.

`NativeSessions` creates fresh identities and enforces explicit model options before delivery. Method schedulers own activation policy, continuation and completion. Generic execution records and opaque method references use stable attempt IDs. Unknown launches/jobs never replay automatically. The Python `JobLedger` deduplicates requests, verifies assets and records results; it does not duplicate method quota deductions. All methods use the same project queue scope, and existing lock markers preserve three-slot compatibility.

Tests use mock native loops and deterministic executors. Real Docker solving is not part of default verification.
