# Dynamic research acceptance fixture

This independently installable method depends only on `@onefav/research-runtime@1.0.0` and DSH, never AnySolvers. It accepts its own `dynamic-fixture-v1` input contract. It does not accept AnySolvers sealed initialization as if those contracts were interchangeable.

In a disposable project create `configs/dynamic-fixture.json`:

```json
{"format":"dynamic-fixture-v1","testOnly":true,"studyId":"dynamic-demo","values":[1,2]}
```

Load the execution package's service/UI and this bundle with a Python interpreter. Use preset `dynamic-fixture-main` and `/as-research --check`, then `/as-research`. No model message or candidate executable is run. The fixed test executor measures two JSON configurations on v1, then seals v2 using its own update rule and the first measurement evidence. Both configurations are remeasured on the common v2 set. The view displays each set separately, and selection compares v2 only.

`schemas/delivery.json` is read and enforced on both write and status. Tests reject missing and mistyped delivery fields. Data snapshots are verified by hash; updates never overwrite a snapshot. All records explicitly say `testOnly:true`. These tests prove structural extensibility, not scientific effectiveness.

The new method has its own preset, command instructions, state, delivery schema, selection code and native client module. Installed Python imports execution helpers from the installed runtime dependency. Build with `npm run build` inside this package after installing its dependency; no copy of runtime code is required.
