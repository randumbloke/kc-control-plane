# kc-control-plane

Control plane of the Decentralised Data Architecture (DDA) reference deployment on Google Cloud:
the **control evaluator** (CDMC controls C12 and C14 for phase 1), the **contract writer**, the
shared library **`cdmc_common`**, the canonical **JSON Schemas** of the domain repositories'
declarative files, and the **`kc-validate`** CLI that checks them.

Specification: `cdmc_kc` repository, `docs/design/dda_technical_design.md` §2.5 (event
topology), §3.2.1 (contract inputs), §5.1–§5.4.1 (components, findings, C12, C14), §5.11
(aspect types), §6.10 (images), §6.17 (phase 1 scope); shared contracts in
`deployment/contracts/`. Change `mvp-phase-1-build`, tasks 0.3 and 7.1–7.3.

All pass/fail logic is deterministic, versioned and covered by golden fixtures (D-01). No LLM is
called anywhere. Findings carry an authoritative `evaluation` block; the advisory `narrative`
block is always empty in phase 1 and is never part of the published message (P5-2).

## Layout

```text
schemas/                   canonical JSON Schemas (copied from cdmc_kc deployment/contracts/schemas)
src/cdmc_common/           shared library, every external API behind an interface
  config.py                Settings from environment variables (phase 1 defaults)
  transport.py             google-auth + requests JSON client with bounded retry
  catalog.py               lookupEntry; whole-aspect write (entries.patch, aspectKeys)
  lineage.py               searchLineageStreaming (UPSTREAM)
  domains.py               data domains adapter (Preview; native-shaped objects; only caller)
  findings.py              Finding model, status mapping, idempotency key, Pub/Sub publisher
  aspects.py, aspect_types/cdmc_aspect_types.yaml   the five §5.11 templates + payload validator
  bigquery.py, scans.py, storage.py, tasks.py, products.py, names.py
src/evaluator/
  service.py               Cloud Run service (Flask): /events/kc, /events/state, /tasks/rollup
  sweep.py                 Cloud Run job: daily sweep, sharded by CLOUD_RUN_TASK_INDEX/COUNT
  handlers.py              framework-free request handling; module flags (evaluator_modules)
  c14/rules.py, module.py  §5.4.1 resolution and rule order; cdmc-lineage-evidence writer
  c12/rules.py, module.py  §9.5 pooled score, status order, freshness; coverage; quality writer
  contract_writer.py       §3.2.1 only writer of cdmc-data-product-contract
  uploader.py              §2.3.2 uploader check (domain pipeline, or tf-deployer when single_deployer)
  manifests.py             design-time lineage manifests (transitive design-time origins)
src/kc_validate/           kc-validate CLI and schema loader
tests/                     pytest; golden/c12, golden/c14 fixtures; fakes.py; fixtures/
build/                     Dockerfile (targets service, job), cloudbuild.yaml, write_images_json.py
.github/workflows/ci.yml   ci-checks: ruff, pytest, schema check, Docker build
```

## Running the checks

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run kc-validate schemas
uv run kc-validate repo ../dda-customer --env dev      # a domain repository
uv run kc-validate file images.json --schema images    # one file
```

`kc-validate repo` validates `datasets.yaml`, `products/*.yaml`, `dq/<dataset>/<table>.yaml` and
`pipelines/lineage/manifest.json`, after replacing `{env}`, then runs cross-file checks: dataset
names match their stage and business; product IDs match file names; asset tables are in declared
`analytics` datasets; asset access groups are declared; at most 10 approvers; `Irregular` cadence
has `cron_schedule`; DQ file paths match `table`; manifest domain and target datasets are declared;
no placeholder other than `{env}` remains.

## Runtime

| Component | Entry point | Trigger (created by `cdmc-governance`) |
|---|---|---|
| Evaluator service (`evaluator` image) | gunicorn `evaluator.service:app_factory()` | Pub/Sub push `kc-evaluator` → `POST /events/kc`; Eventarc audit trigger on state bucket `products/`, `lineage/` → `POST /events/state`; Cloud Tasks `product-rollups` → `POST /tasks/rollup` |
| Evaluator sweep (`evaluator-sweep` image) | `evaluator-sweep` | Cloud Scheduler, daily 02:00 `Australia/Sydney`, 20 tasks |

Environment variables (defaults are the phase 1 estate): `DDA_ENV`, `HUB_PROJECT_ID`, `REGION`,
`CICD_PROJECT_ID`, `HUB_STATE_BUCKET`, `FINDINGS_TOPIC`, `DQ_RESULTS_TABLE`, `ROLLUP_QUEUE`,
`ROLLUP_HANDLER_URL`, `ROLLUP_INVOKER_SA`, `ROLLUP_AUDIENCE` (optional; default the base URL of
`ROLLUP_HANDLER_URL`, as Cloud Run requires), `ROLLUP_WINDOW_SECONDS`, `DATA_PROJECTS`,
`SINGLE_DEPLOYER`, `SCHEDULER_TIME_ZONE`, `EVALUATOR_MODULES` (`c12,c14`). Both run as
`evaluator-<env>`; credentials come from the metadata server.

Module versions (recorded as `evaluator_version`): `c14-lineage@1.0.0`, `c12-coverage@1.0.0`,
`c12-rollup@1.0.0`, `contract-writer@1.0.0`, `c14-manifest-source@1.0.0`. A change that alters a
golden-fixture outcome raises the version (§5.10).

## Deployment

Merges to `main` pass `ci-checks` and the merge gate; the `kc-control-plane-image` Cloud Build
trigger (bootstrap-owned) then runs `build/cloudbuild.yaml`: checks, builds both targets, pushes
`australia-southeast1-docker.pkg.dev/pltfrm-ngnrng/dda-control-plane/{evaluator,evaluator-sweep}:<sha>`,
and writes `outputs/kc-control-plane/images.json` (schema `images`) plus the marker
`promotions/kc-control-plane/image/<sha>.json` to `gs://tfstate-pltfrm-ngnrng`. `cdmc-governance`
deploys by digest from its `envs/images.json`. This repository holds no Terraform (§6.1).

## Design interpretations and open questions

- **C14 intermediate nodes.** A node with no upstream link is a run-time ultimate source. A path
  cut at 20 hops, or one that only loops back on itself, ends at an intermediate node that is not
  a source; a table whose lineage ends only there is `PENDING` (< 48 h) or
  `INSUFFICIENT_EVIDENCE`. The search asks for 21 hops so a node at hop 20 is known to have more
  upstream.
- **Design-time origins.** A manifest declares each target's direct sources; the evaluator expands
  declared sources that are themselves targets of the same manifest, so design-time and run-time
  ultimate sources have the same form.
- **C14 coverage (OD-6, resolves DQ-1).** A run-time ultimate source is covered when it is in the
  table's declared manifest set, or when every walked upstream path from the table to it passes
  through a node that is in the declared set, belongs to another domain and is a provisioning
  point. A consuming domain declares the upstream product's asset, not that domain's landing
  paths, so a table built from Customer Profile is `VERIFIED` (golden fixture `c14_13`, AT-23). A
  source also reachable by a path that bypasses the provisioning point is not covered (`c14_16`),
  and covering one source does not cover another undeclared one (`c14_15`). The evidence still
  lists every run-time ultimate source; the finding detail lists the covered ones in
  `covered_by_provisioning_point`. Paths not walked (cut at 20 hops, or beyond the walk's visit
  cap) cannot bypass the provisioning point.
- **Provisioning point in phase 1.** `cdmc-provisioning-point` has no writer until C3; when it is
  absent, C14 applies the C3 definition (asset of a published product in an `analytics` dataset)
  to record `upstream_data_products` (AT-23).
- **Rejection findings.** Uploader-check rejections use checks `C12.contract_source` (asset type
  `DATA_PRODUCT`) and `C14.manifest_source` (asset type `DATA_DOMAIN`); §5.2 has no object asset type.
- **Manifest trust in the sweep.** The uploader check runs on the upload event; the sweep reads
  the current manifest object without re-checking its uploader (managed-folder IAM is the first
  control).
- **Findings encoding.** Messages are plain JSON with `evaluation.detail` as a JSON string, which
  suits a BigQuery subscription using the table schema. If the `findings` topic gets an Avro
  schema with nullable unions, JSON encoding needs union-wrapped values (`{"double": 95.0}`);
  `Finding.to_message` is the one place to change (VA-61).
- **Product assets** come from the published product file (uploader-checked), not from
  `dataProducts.dataAssets.list`.

## VERIFY IN BUILD markers

| ID | Where | What |
|---|---|---|
| VA-09 | `evaluator/handlers.py` | Metadata feed payload shape (entry name is searched for) |
| VA-09 | `evaluator/uploader.py` | Eventarc audit-log event shape for `storage.objects.create` (no dedicated VA) |
| VA-22 | `cdmc_common/config.py` | Lineage within 24 h; 48 h `PENDING` bound |
| VA-34 | `evaluator/c12/module.py` | `cdmc-critical` attribute reaches the export as `rule_attributes` |
| VA-35 | `evaluator/c12/module.py` | One central export table; export column names in `RESULTS_SQL` |
| VA-42 | `cdmc_common/catalog.py` | Evaluator cannot write system aspects |
| VA-47 | `evaluator/c12/module.py` | Lineage links base tables to views (view assets) |
| VA-48 | `cdmc_common/domains.py` | `evaluator-<env>` can list domains and bindings |
| VA-56 | `cdmc_common/products.py`, `evaluator/c12/rules.py` | Product location; `refresh-cadence` field names, value case, time zone |
| VA-61 | `cdmc_common/findings.py` | `evaluation.detail` JSON string to a JSON column |
| VA-64 | `cdmc_common/lineage.py`, `names.py`, `c14/rules.py` | REST stream shape and hub-parented cross-project search; Cloud Storage FQN form (no dedicated VA) |
| VA-76 | `cdmc_common/catalog.py`, `config.py`, `names.py` | `ddaAspectWriter` writes custom aspects; aspect key with project ID; entry location |
| estate | `build/Dockerfile`, `tests/estate.py` | Base image digests; subdomain IDs |
