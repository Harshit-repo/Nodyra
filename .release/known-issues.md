# Known issues for 0.1.0

- Airflow and Prefect can create reviewed partial drafts for a bounded set of
  common synchronous patterns; dynamic mapping plus state/retry/schedule hooks
  still require explicit migration.
- Disposable hosted evaluation is a deployable operator profile, not a public
  Nodyra-managed service.
- Community registry installs currently target `venv` environments; Conda and
  container-backed environments require separate signed-build support.
- The product is Beta. There is no LTS line and only the latest Beta receives
  routine fixes.
