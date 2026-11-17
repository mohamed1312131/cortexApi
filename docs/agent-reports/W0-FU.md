# W0-FU — Wave 0 follow-up: agent report

Branch: `wave-0-fu` (from `main` @ ae09ab5) · Date: 2026-11-17 · Brief: TASK W0-FU
(fail-closed auth, settings isolation, frozen data)

Not pushed, not merged. `main` is untouched.

## 1. Result

| Step | Status |
|---|---|
| 1. Auth fails closed | **Done** |
| 2. `.env` from the package location | **Done** |
| 3. Test isolation (one autouse fixture) | **Done**, with one change in `app/config.py` that the reviewer should look at (decision D8) |
| 4. Frozen shared data | **Done** |
| 5. `/ready`: generic codes, detail in logs only | **Half done.** Logging: done. Generic response code: **stopped**, it needs a change in a read-only file (section 10) |
| 6. Docs only when `APP_ENV=local` | **Done** |

| | `main` ae09ab5 | `wave-0-fu` |
|---|---|---|
| Offline suite, no `.env` | 652 passed | **805 passed** |
| Offline suite, `.env` with `CORTEX_API_KEYS_JSON` | 34 failed, 618 passed | **805 passed** |
| Offline suite, `.env` with `APP_ENV=production` | 45 failed, 607 passed | **805 passed** |
| Layer 2 output, six sample requests | reference | **byte-identical** |
| Layer 2 multi-mode build, warm | 0.0044 s | **0.0046 s** |
| Layer 2 multi-mode build, first in a process | 0.136 s | **0.246 s** (one-time freeze of the data) |

805 = 652 existing − 1 replaced + 154 new. No pre-existing test outside
`tests/foundation/` was edited, deleted, skipped or marked xfail.

## 2. Working-folder guard

Run before the first change and again before every commit, always with this
output:

```
$ cd /Users/rouge/Desktop/pfe/Cortex-Platform/cortex-api && pwd && git remote -v && git branch --show-current
/Users/rouge/Desktop/pfe/Cortex-Platform/cortex-api
origin	https://github.com/mohamed1312131/cortexApi.git (fetch)
origin	https://github.com/mohamed1312131/cortexApi.git (push)
wave-0-fu
```

Nothing was read or written in `../Backend` or `../cortex-frontend`. Outside
`cortex-api/` only the spec (§16, §22) was read. The real `cortex-api/.env` was
never opened, printed, copied or edited. Every `.env` used below is a synthetic
file with dummy values, in a throwaway export (`git archive … | tar -x`) under
the session scratch folder. No container was started or touched; no host port
other than a private one (18731, loopback) was used.

## 3. Commits

```
674b353 style(w0-fu): wrap three test lines longer than 100 characters
3b930c6 feat(w0-fu): freeze the index of loaded Layer 2 assets too
45221c3 fix(w0-fu): read no .env inside a pytest session, not even at import
4324fca fix(w0-fu): /ready logs the full failure detail with credentials masked
d1547ae feat(w0-fu): freeze the shared Layer 2 data
a95e176 feat(w0-fu): serve /docs, /redoc and /openapi.json only when APP_ENV=local
5a6262b fix(w0-fu): auth fails closed; settings pinned for every test
3a0b325 fix(w0-fu): read .env from the package location, not the working directory
```

plus the commit that adds this report.

## 4. What changed

### Step 1 — auth fails closed

| File | Change |
|---|---|
| `app/config.py:45` | `app_env` default is `"production"` (was `"local"`). This is how "both explicit" is implemented: `local` only ever comes from someone setting it |
| `app/config.py:72` | New setting `cortex_auth_disabled: bool = False` (env `CORTEX_AUTH_DISABLED`) |
| `app/config.py:120` | `Settings.is_local`: true only for `APP_ENV=local` (case and spaces ignored, as in W0) |
| `app/core/auth.py:68` | `AuthConfig(enabled, keys)`; keys are a read-only mapping |
| `app/core/auth.py:118-138` | `_resolve_auth()`, the whole rule (table below) |
| `app/core/auth.py:112` | `_settings_file_hint()`: the startup error says where the settings file was looked for and whether it was found |
| `app/core/auth.py:155-178` | `validate_api_key_config()`: warning `auth.disabled` when off, warning `auth.no_api_keys` when on with no key (nothing can pass) |
| `app/core/auth.py:191-199` | `require_api_key()`: open only when `AuthConfig.enabled` is false |
| `.env.example:10-17` | `APP_ENV` comment rewritten (the W0 text said local = auth off) |
| `.env.example:59-76` | Key placeholder `{"PASTE_SHA256_HEX_OF_YOUR_API_KEY_HERE":"tenant-local"}`; `CORTEX_AUTH_DISABLED=false` |

| `APP_ENV` | keys | `CORTEX_AUTH_DISABLED` | Result |
|---|---|---|---|
| anything | configured | false | auth on: no key 401, wrong key 403, valid key 200 |
| not `local` (including unset) | none | false | **refuses to start** |
| not `local` (including unset) | any | true | **refuses to start** |
| `local` | none | false | starts; `/api/v1` answers **401** without a key, 403 with any key |
| `local` | any | true | auth off (tenant `local-dev`), warning logged at startup |

Checked over real HTTP as well (uvicorn on 127.0.0.1:18731, exported code,
synthetic environment, database and Redis URLs pointing at a closed port):

```
nothing set                         did not start, exit 3   ApiKeyConfigError: CORTEX_API_KEYS_JSON is empty and APP_ENV='production' …
APP_ENV=local                       /api/v1 401 (no key) 403 (any key)   /docs 200
APP_ENV=local + DISABLED=true       /api/v1 passes auth                   /docs 200
APP_ENV=production + one key        401 / 403 / passes auth with the key  /docs /redoc /openapi.json 404
APP_ENV=production + DISABLED=true  did not start, exit 3   ApiKeyConfigError: CORTEX_AUTH_DISABLED=true is only accepted together with an explicit APP_ENV=local …
CORTEX_AUTH_DISABLED=true only      did not start, exit 3   (same error, APP_ENV is 'production')
```

`python -m app.core.preflight` (what the container entrypoint runs): exit 1 and
one line `preflight failed: ApiKeyConfigError: …` with nothing set; exit 0 with
`APP_ENV=local`.

### Step 2 — `.env` from the package location

| File | Change |
|---|---|
| `app/config.py:8,15` | `PACKAGE_ROOT` = the folder that contains `app/`; `ENV_FILE = PACKAGE_ROOT / ".env"` |
| `app/config.py:24,114` | `Settings` reads `default_env_file()` = `ENV_FILE` (was the relative `".env"`) |

Rule implemented: **`<repository>/.env` is read when it exists, whatever the
working directory is. The file is optional.** When it does not exist (the Docker
image has none; compose passes the environment) settings come from the process
environment and the code defaults. There is no "required" switch. A start with
no file and no environment is not silent: `APP_ENV` is then `production`, there
are no keys, and the process refuses to start with an error that names the
settings file path and says "not found".

### Step 3 — test isolation

| File | Change |
|---|---|
| `tests/conftest.py:41-96` | The one autouse fixture, `pinned_settings` |
| `tests/conftest.py:27-37` | `PinnedSettings`, a small NamedTuple the fixture yields (test key, tenant) so `real_auth` tests can use the pinned key. Not a fixture |
| `tests/foundation/conftest.py:18` | Registers the `real_auth` marker (`pyproject.toml` is read-only) |
| `app/config.py:19-38` | `default_env_file()` returns no file inside a pytest session (decision D8) |

What the fixture does, for every test:

1. Environment: `APP_ENV=test`, `CORTEX_API_KEYS_JSON` = one known test key,
   `CORTEX_AUTH_DISABLED=false`, `LANGSMITH_TRACING=false`,
   `LANGCHAIN_TRACING_V2=false`.
2. No `.env` file is read (`Settings.model_config["env_file"] = None`).
3. Clears the cached settings object (`get_settings.cache_clear()`), rebuilds it
   from the pinned environment and the code defaults, and writes every one of
   its 44 values onto the shared `app.config.settings` object, which 13
   application modules hold by reference. `monkeypatch` puts the original values
   back after the test. The auth cache is cleared before and after.
4. Auth: see decision D6.

### Step 4 — frozen shared data

| File | Change |
|---|---|
| `app/services/layer2/data_catalog.py:77-142` | `FrozenDataError(TypeError)`, `FrozenDict(dict)`, `FrozenList(list)`: every modifying method raises |
| `app/services/layer2/data_catalog.py:148` | `freeze()`: deep conversion of parsed JSON |
| `app/services/layer2/data_catalog.py:534` | The 32 used payloads are frozen once, at load |
| `app/services/layer2/data_catalog.py:538-539` | `main_assets` and the `payloads` index are `FrozenDict` |
| `app/services/layer2/data_catalog.py:382-387` | `DataAsset.top_keys`, `first_record_keys`, `notes` are `FrozenList` |
| `app/services/layer2/data_catalog.py:628,657-658` | A late-loaded asset is frozen and added through `_add_payload()`, which never replaces a key |

`app/core/data_paths.py` did not need a change.

Why subclasses and not `MappingProxyType` / tuples: connectors are read-only
for this task and test `isinstance(x, dict)` / `isinstance(x, list)` everywhere
(for example `sea_cost_connector.py:70,264,266`). With a proxy or a tuple those
checks fail and a found record becomes an unknown. A subclass passes them, and
no read method is overridden, so reads cost what they cost before.

### Step 5 — `/ready`

| File | Change |
|---|---|
| `app/core/readiness.py:37-73` | `redact_credentials()`: masks the user:password part of any URL, `password=…` pairs, and every literal occurrence of the passwords in `DATABASE_URL` / `REDIS_URL` (as written and percent-decoded) |
| `app/core/readiness.py:104-113` | A failed check logs type, message, cause chain and traceback at WARNING, masked |
| `app/core/readiness.py:116` | **Unchanged**: the response still says `error: <ExceptionClass>` (section 10) |

Can driver text contain the URL or the password? Checked with the real drivers
against a closed local port (`127.0.0.1:1`, nothing real contacted):

```
database  ConnectionRefusedError: [Errno 61] Connect call failed ('127.0.0.1', 1)
redis     redis.exceptions.ConnectionError: Error 61 connecting to 127.0.0.1:1. Connect call failed ('127.0.0.1', 1).
```

Host and port, no user, no password, no URL. That is one failure mode on one
driver version, not a guarantee, so the masking is applied anyway.

### Step 6 — docs

| File | Change |
|---|---|
| `app/main.py:76-99` | `create_app()`: `openapi_url`, `docs_url`, `redoc_url` are passed to FastAPI only when `settings.is_local`, otherwise `None` (FastAPI's documented way, https://fastapi.tiangolo.com/how-to/conditional-openapi/). `app = create_app()` |

## 5. Acceptance

`python -m pytest -q -m "not live_llm"` on a clean export of the last code
commit, 674b353 (`git archive wave-0-fu`), scrubbed environment (`env -i`), a
venv built from `uv.lock` (`uv sync --frozen`), run with `uv run --frozen`:

| `.env` in the checkout | Summary line |
|---|---|
| none | `805 passed, 2 deselected, 3 warnings in 32.61s` |
| `CORTEX_API_KEYS_JSON={"<sha256>":"tenant-dev"}` | `805 passed, 2 deselected, 3 warnings in 31.00s` |
| `APP_ENV=production` | `805 passed, 2 deselected, 3 warnings in 20.66s` |
| extra: 22 non-default values (below) | `805 passed, 2 deselected, 3 warnings in 20.36s` |
| extra: none, with the CI job's environment (`APP_ENV=local LANGSMITH_TRACING=false LLM_PROVIDER=none`) | `805 passed, 2 deselected, 3 warnings in 19.83s` |
| extra: the developer checkout with its real `.env` (not opened; summary line only) | `805 passed, 2 deselected, 3 warnings in 20.39s` |

Same files on `main` ae09ab5: keys `34 failed, 618 passed`; production
`45 failed, 607 passed`; the 22-value file `250 failed, 402 passed` (run with
name resolution blocked in-process; it attempted none).

The 22-value file sets `APP_ENV=local`, keys, `CORTEX_AUTH_DISABLED=true`,
`LANGSMITH_TRACING=true` with an empty key (if it were ever honoured the app
would refuse to start instead of sending anything), `LLM_PROVIDER=google` and
per-layer providers with dummy keys, `CORTEX_REDIS_FALLBACK_ENABLED=false`,
`ORCHESTRATOR_CACHE_ENABLED=false`, `DATABASE_URL` / `REDIS_URL` on a closed
port, `CORTEX_DATA_DIR=/nonexistent-hostile-data-dir`, `API_PORT`, token and
size limits. It is what found the import-time problem of decision D8: before
that commit it gave `6 failed, 796 passed`.

| Acceptance item | Status | Evidence |
|---|---|---|
| Suite passes with no `.env` / keys in `.env` / `APP_ENV=production` in `.env` | **Verified** | Table above |
| New tests: fail-closed matrix | **Verified** | `tests/foundation/test_api_key.py`, section 6 |
| New tests: other-cwd settings load | **Verified** | `tests/foundation/test_settings_location.py` |
| New tests: frozen data | **Verified** | `tests/foundation/test_frozen_data.py` |
| New tests: `/ready` no leak | **Verified** for what is implemented | `tests/foundation/test_ready_no_leak.py`. The class name in the response is still there (section 10) |
| New tests: docs hidden outside local | **Verified** | `tests/foundation/test_docs_exposure.py` |
| Revert-check auth and frozen data | **Verified** | Section 7 |

## 6. New tests (154) and what each covers

| File | Tests | Requirement |
|---|---|---|
| `tests/foundation/test_api_key.py` (+37, −1) | Matrix on a real route and on all 7 `/api/v1` routes: local/no keys → 401 (403 with a key), process starts; local + explicit disable → 200 with tenant `local-dev`, warning logged; production, staging, test, dev, empty with no keys → startup error naming the variable; disable outside local, with or without keys → startup error, and a request is refused even if startup were bypassed; default `APP_ENV` is not local; nothing configured → startup error; disable alone → startup error; the 12-row truth table of `_resolve_auth`; local + keys keeps 401/403/200; keys mapping is read-only. Valid key → 200 and wrong key → 403 are W0's tests, kept, now running under `real_auth` | Step 1 |
| `tests/foundation/test_env_example.py` (+4) | `.env.example` has `CORTEX_AUTH_DISABLED=false`; an unedited copy gives auth on and a start refused (placeholder); wording for auth and docs | Step 1 |
| `tests/foundation/test_settings_location.py` (11) | In fresh interpreters on a throwaway copy of `app/config.py` next to a synthetic `.env`: same settings from two working directories; a `.env` in the working directory is not read; environment variables still win; a missing file is allowed (Docker); missing file + empty environment gives the closed defaults and a startup error that names the file; inside a pytest session the file is not read at import | Step 2, step 3 |
| `tests/foundation/test_settings_isolation.py` (16) | Pinned values; auth on with exactly the pinned key; tracing off at app start; no `.env` read (also not from the working directory); cached object rebuilt; all 44 fields equal a build with no file; a change made by one test does not reach the next; the test auth dependency answers with the pinned tenant; `real_auth` keeps the real dependency (401/403/200 with the pinned key); the override is removed after each test; and, per synthetic `.env` (keys / production / everything different), a real pytest session in a throwaway copy of `app/` + `tests/conftest.py` that checks the pinned values, the engine URL, the Redis store target and fallback flag, the docs routes of the imported app and a `/api/v1` call | Step 3 |
| `tests/foundation/test_frozen_data.py` (37) | Every container of the 32 loaded assets is frozen at every depth (26 865 dicts, 29 295 lists); 12 kinds of dict modification and 13 of list modification raise `FrozenDataError`; late-loaded assets, the asset index, `main_assets` and `DataAsset` list fields are frozen; payloads equal the files on disk and serialise to the same JSON; `isinstance`, `get`, iteration, indexing behave as for plain containers; `dict(x)`, `x.copy()`, `{**x}`, slices, `sorted`, `copy.copy`, `copy.deepcopy`, pickle give plain independent copies; a shallow copy does not unfreeze nested shared data; a "run" that tries to rewrite all 56 160 containers is refused every time and the next build is identical; Layer 2 output (6 packages + 126 connector calls) is identical with frozen and with plain data | Step 4 |
| `tests/foundation/test_ready_no_leak.py` (20) | The response has no message, URL, host, user, database name, path or traceback for a failed database, Redis or data check; the log has type, message, cause chain and traceback; URL credentials, bare configured passwords (plain and percent-encoded) and `password=` pairs are masked; a timeout is logged; `redact_credentials` unit cases | Step 5 |
| `tests/foundation/test_docs_exposure.py` (29) | `/docs`, `/redoc`, `/openapi.json` → 200 with `APP_ENV=local`; 404 (and `/docs/oauth2-redirect`) with production, staging, test, dev, empty and unset; hiding the docs removes no API route; visible docs do not open `/api/v1`; the app object uvicorn serves follows `APP_ENV` in a fresh interpreter | Step 6 |

## 7. Revert-checks

Each one: fresh copy of the final commit, one behaviour put back, tests run.
A check "bites" when tests fail.

The numbers are from commit 3b930c6; the only later code commit (674b353)
re-wraps three test lines.

Auth and frozen data, full suite (805 tests):

| Behaviour put back | Result | Tests that fail |
|---|---|---|
| Auth: local + no keys is open again (the W0 behaviour) | 11 failed, 794 passed | `test_local_without_keys_starts_and_refuses_every_request`, `test_local_without_keys_refuses_every_api_v1_route` ×7, `test_local_without_keys_logs_that_nothing_can_pass`, the truth-table test, `test_docs_being_visible_does_not_open_the_api` |
| Auth: the disable flag is honoured outside local | 13 failed, 792 passed | `test_disable_outside_local_is_a_startup_error` ×10, `test_disable_outside_local_never_serves_api_v1`, `test_disable_alone_without_an_explicit_app_env_refuses_to_start`, the truth-table test |
| Config: `APP_ENV` defaults to `local` again | 9 failed, 796 passed | `test_app_env_is_not_local_unless_it_is_set`, `test_nothing_configured_refuses_to_start`, `test_disable_alone_…`, `test_docs_are_hidden_when_app_env_is_not_set`, the closed-defaults tests, the three throwaway-copy sessions |
| Auth: a request without a key is accepted | 20 failed, 785 passed | `test_missing_key_is_401`, `test_empty_key_is_401`, `test_every_api_v1_route_rejects_a_missing_or_wrong_key` ×7, the local/no-keys tests, `test_real_auth_mark_keeps_the_real_dependency` |
| Data: payloads not frozen at load | 30 failed, 775 passed | `test_every_loaded_payload_is_frozen_at_every_depth`, the 25 modification tests, `test_one_run_cannot_change_what_another_run_reads`, 3 more |
| Data: only the top level frozen | 32 failed, 773 passed | The same, plus `test_freeze_helper` and `test_late_loaded_asset_is_frozen_too` |
| Fixture: values of the shared settings object not replaced | 64 failed, 741 passed | 35 pre-existing tests under `tests/layer3` and `tests/orchestrator`, `tests/test_health.py` ×6, and 23 in `tests/foundation/` (no `.env` → `APP_ENV=production`, no keys → startup refused) |

Others, `tests/foundation` + `tests/test_health.py` (230 tests):

| Behaviour put back | Result | Tests that fail |
|---|---|---|
| Data: `FrozenList.append` allowed | 3 failed | modification test for `append`, catalogue and late-load tests |
| Data: copies stay frozen | 2 failed | `test_copies_are_plain_and_belong_to_the_caller`, `test_deep_copy_is_plain_at_every_depth_and_independent` |
| Data: the asset index is a plain dict | 2 failed | `test_catalogue_entries_are_read_only`, `test_late_loaded_asset_is_frozen_too` |
| Fixture: `.env` read again during a test | 4 failed | the three throwaway-copy sessions, `test_no_env_file_is_read_during_a_test` |
| Config: `.env` read at import inside pytest | 3 failed | `test_env_file_is_not_read_at_import_inside_a_pytest_session`, `test_pytest_sets_the_session_variable_before_the_app_is_imported`, the "everything different" session |
| Config: `.env` relative to the working directory (W0) | 7 failed | 7 of the 11 tests of `test_settings_location.py` |
| Docs always exposed | 26 failed | `test_docs_are_hidden_outside_local` ×20 and 6 more |
| `/ready`: log not masked | 4 failed | the three masking tests (one runs twice) |
| `/ready`: exception message put in the response | 5 failed | 3 in `test_ready_no_leak.py` and W0's `test_not_ready_when_a_dependency_is_unreachable` ×2 |

Every check bites.

## 8. Layer 2 equivalence and timing

W0's report gives a sha256 (`4332e107…`) for the six sample requests but the
recipe that produced it is not in the repository, and I could not reproduce
that exact value on `main` with five serialisations I tried. So the comparison
below uses one recipe on both sides, `main` ae09ab5 and `wave-0-fu`:

```python
# PYTHONPATH=<checkout> python recipe.py   (cwd = the checkout)
import hashlib
from app.schemas import RequestedMode
from app.services.layer2.registry import BLOCK_REGISTRY
from app.services.layer2.service import build_fact_package_for_request
from tests.foundation.conftest import _dangerous_goods_request, _single_mode_request
from tests.prompt_budget_helpers import multimode_request

def requests():
    return [multimode_request(), _dangerous_goods_request("UN3480"), _dangerous_goods_request("UN1203"),
            _single_mode_request(RequestedMode.air), _single_mode_request(RequestedMode.sea),
            _single_mode_request(RequestedMode.road)]

packages = "".join(build_fact_package_for_request(r).model_dump_json() for r in requests())
direct = "".join(BLOCK_REGISTRY[b](r).model_dump_json() for b in sorted(BLOCK_REGISTRY) for r in requests())
print(hashlib.sha256(packages.encode()).hexdigest(), len(packages.encode()))
print(hashlib.sha256(direct.encode()).hexdigest(), len(direct.encode()))
```

| | `main` ae09ab5 | `wave-0-fu` |
|---|---|---|
| Six `FactPackage`s, concatenated JSON (275 925 bytes) | `2c00fa7f122929c7c362d2a4a9e7b50e8be49b2a2842cb05c108e0b1dd0f5331` | same |
| 126 direct connector calls (21 connectors × 6 requests, 310 206 bytes) | `2d69d0136ae8978c1c33669eb775af5b7a3e2dbf5b446d54e3b8b023956f67c5` | same |
| Block statuses over the six packages | found 25, not_applicable 2, unknown 51 | same |

A second pass in the same process gives the same bytes on both sides.

**(b) Connectors that copy a record and change the copy.** No connector does
this today: there is no `deepcopy`, `copy.copy`, `.copy()` or `dict(record)` in
`app/services/layer2/`. A static scan of the 21 connectors found 271 in-place
operations (`append`, `extend`, `x[k] = …`, …); 253 are on containers created
in the same function, and I read the other 18 by hand: all are local lists
returned by helper functions or the `reasons` list of a `BlockConfidence` built
on the spot. None touches loaded data. The copy behaviour itself is covered by
`test_copies_are_plain_and_belong_to_the_caller` and
`test_deep_copy_is_plain_at_every_depth_and_independent`.

**Shared objects travel inside the output.** 16 fields of `BlockResponse.data`
hold lists or dicts that are the shared store's own objects (for example
`carrier_examples[].service_capability_tags`, `rate_usd_per_kg`,
`realistic_transit_reference.assumptions`). On `main` any later code that
modified one in place would have silently changed the data for every following
run. Now it raises `FrozenDataError`. A scan of the non-connector code under
`app/` found no such modification, and the full suite passes.

**(c) Timing**, `tests/prompt_budget_helpers.multimode_request()`, one process
per run, first build then the median of 5 more:

| Run | `main` first | `main` warm | `wave-0-fu` first | `wave-0-fu` warm |
|---|---|---|---|---|
| 1 | 0.1662 s | 0.0044 s | 0.2823 s | 0.0046 s |
| 2 | 0.1359 s | 0.0043 s | 0.2484 s | 0.0046 s |
| 3 | 0.1359 s | 0.0044 s | 0.2429 s | 0.0047 s |
| 4 | 0.1361 s | 0.0044 s | 0.2428 s | 0.0045 s |
| 5 | 0.1366 s | 0.0046 s | 0.2463 s | 0.0045 s |
| median | **0.136 s** | **0.0044 s** | **0.246 s** | **0.0046 s** |

Runs alternate between the two checkouts (same machine state). Earlier in the
session, with the machine busier, the same script gave `main` 0.221 s / 0.0071 s
and `wave-0-fu` 0.377 s / 0.0072 s, which is the level of W0's own figures
(warm ≈ 0.007 s, first ≈ 0.22–0.28 s). Absolute values moved with the machine;
the difference between the two checkouts did not.

The warm build costs 0.0001–0.0002 s more (the output holds `dict`/`list`
subclass instances, which serialise slightly slower than exact types). The
first build in a process pays the freeze once: 0.10–0.14 s for the 36.2 MB in
use (56 160 containers). There is no per-request copy. Each uvicorn worker pays
it once at startup.

## 9. Implied ownership

Files outside the brief's list that a numbered step could not be done without:

| File | Step | Why |
|---|---|---|
| `.env.example` | 1 | The brief names it: placeholder and `CORTEX_AUTH_DISABLED=false` |
| `app/core/readiness.py` | 5 | It is the `/ready` implementation |

`app/core/preflight.py` and `app/core/data_paths.py` were in scope if needed and
did not need a change.

W0 tests updated because the required behaviour changed:

| File:line on `main` | Change | Reason |
|---|---|---|
| `tests/foundation/test_api_key.py:265-273` | `test_no_keys_in_local_env_switches_auth_off` (asserted 200) is replaced by `test_local_without_keys_starts_and_refuses_every_request` (asserts 401; now at `:295`) | Step 1: local + no keys must be 401 |
| `tests/foundation/test_api_key.py` (top) | `pytestmark = pytest.mark.real_auth` added (now `:38`). No assertion changed | Step 3: these tests must run against the real dependency |
| `tests/foundation/test_env_example.py:124` | `assert active[API_KEYS_ENV] == ""` becomes the placeholder (now `:125`) | Step 1: placeholder present in `.env.example` |

No other existing assertion changed.

## 10. Stops on read-only files

1. **`/ready` generic status codes — not done.** `tests/test_health.py:58`
   asserts `body["checks"][name] == "error: ConnectionRefusedError"`. That file
   is outside `tests/foundation/` and read-only, and no generic code can satisfy
   that assertion. Needed change, two lines:
   - `app/core/readiness.py:116`: `return f"error: {type(exc).__name__}"` → `return "error"`
   - `tests/test_health.py:58`: `== "error: ConnectionRefusedError"` → `== "error"`

   Today an unauthenticated caller of `/ready` learns the exception class, for
   example `error: ConnectionRefusedError`, `error: ConnectionError`, and with
   other failures driver classes such as an invalid-password error. Nothing else
   leaks. `tests/foundation/test_ready_no_leak.py` does not assert the class
   name, so it will not need an edit when this is changed.
2. **`README.md:104-106`** still says that with `APP_ENV=local` and no key
   authentication is off. Wrong since this branch. It should describe
   `CORTEX_AUTH_DISABLED`, the 401 behaviour and the docs being local-only.
3. **`.github/workflows/ci.yml:23`**: the comment "Same switches as a clean
   checkout without a .env file" is no longer true (`APP_ENV` now defaults to
   `production`). The job itself still passes: its environment gives
   `805 passed` locally.
4. **`docs/postman/cortex_postman_collection.json`** and
   **`scripts/destructive_layer1_runner.py:764`** call `/api/v1` with no
   `X-API-Key`. They now get 401 unless the server runs with `APP_ENV=local`
   and `CORTEX_AUTH_DISABLED=true`.
5. **Settings read while modules are imported**:
   `app/services/layer1/case_state_manager.py:170-172` (Redis URL and fallback
   flag) and `app/core/db.py:7` (database URL). A fixture cannot pin these. I
   did not touch them; the effect is handled in `app/config.py` (decision D8).
   The cleaner fix is in those two files (build the store and the engine on
   first use).

`docker-compose.yml`, `docker/entrypoint.sh`, `Dockerfile`, `pyproject.toml`,
`uv.lock` and the CI workflow needed no change and have none.

## 11. Not verified

- **Docker.** `docker compose up` was not run on this branch. Expected from the
  code and from the preflight command run directly: with a `.env` that has
  `APP_ENV=local` and no keys the container starts and `/api/v1` answers 401;
  with a `.env` that has neither `APP_ENV` nor keys, or an unedited copy of
  `.env.example`, the container exits at the preflight with the error.
- **GitHub Actions.** Nothing was pushed. The job's environment was simulated
  locally on macOS only (no Linux run this time).
- **`live_llm` tests** (2, deselected). The fixture has a separate branch for
  them (they still read the developer's `.env`). That branch was not executed.
- **Driver messages for a wrong password.** Only "connection refused" was
  exercised with the real drivers. An authentication failure needs a running
  Postgres/Redis, and none was started.
- **W0's sha256** (`4332e107…`) was not reproduced; see section 8.
- **One aborted run.** The first "before" run of `main` with the 22-value
  synthetic `.env` was started without a network block and stopped by me after
  about 20 s, when I realised that `LLM_PROVIDER=google` with a dummy key could
  make `main`'s unpinned tests try a provider call. The re-run with name
  resolution blocked shows that `main`'s suite attempts no outbound lookup at
  all with that file, so nothing should have left the machine; the file holds
  dummy values only. I cannot prove it for the aborted run itself.
- **Code that modifies `BlockResponse.data` in place** after Layer 2: none found
  by a static scan and none hit by the suite, but the scan is a heuristic. Such
  code would now raise `FrozenDataError` instead of corrupting shared data.
- **Frozen containers inside a LangGraph checkpoint** (Wave 2): not tried.
- **Thread-level concurrency** of Layer 2 builds: not tested. Freezing removes
  the shared mutable data; it is not a concurrency test.
- **One unexplained slow run.** One full-suite run took `828.10s` instead of
  about 33 s, with the process mostly idle; it could not be reproduced (every other
  full-suite run of the branch took 31–70 s, the slower ones while the machine
  was busy). The suite resolves
  the host name `redis` 14 times in-process, on `main` and on this branch alike
  (the Redis store of `case_state_manager.py:170`); a stalled resolver at about
  57 s per lookup would give that figure. Not confirmed.

## 12. Decisions for the reviewer

- **D1. `APP_ENV` defaults to `production`.** This is how "both explicit" is
  implemented: there is no implicit `local`. Side effect: a start with no
  `.env` and no environment is refused (it used to run open). Alternative
  considered: keep the default `local` and treat only an explicitly set value as
  local (pydantic's `model_fields_set`). Rejected: a second notion of "local",
  and unset `APP_ENV` would still expose `/docs`.
- **D2. `CORTEX_AUTH_DISABLED=true` outside local refuses to start**, rather
  than being ignored. Auth is on in both readings; this one makes the mistake
  visible.
- **D3. local + no keys: 401 without a key, 403 with any key.** The brief says
  401; a presented key that matches nothing is "invalid", as everywhere else.
- **D4. local + explicit disable does not read the keys**, so the
  `.env.example` placeholder may stay when a developer only flips the switch.
- **D5. `.env.example` carries a non-empty placeholder.** An unedited copy no
  longer starts (the placeholder is not a digest). W0's Docker acceptance used
  an unedited copy; that recipe now needs a key hash or
  `CORTEX_AUTH_DISABLED=true`. If "placeholder" meant "the empty entry", set
  the value back to empty and change `tests/foundation/test_env_example.py:125`.
- **D6. The step-3 tension.** Measured first: with auth on and no key, **34**
  pre-existing tests fail with 401 (`tests/layer3/test_layer3_endpoint.py` 14,
  `tests/orchestrator/test_cortex_orchestrator.py` 6, `test_cortex_seam.py` 6,
  `test_cortex_sanitization.py` 5, `test_observability.py` 2,
  `tests/layer3/test_agent_run_tracing.py` 1). Resolved inside the one fixture:
  `app.dependency_overrides[require_api_key]` is set to a function that puts the
  pinned tenant on `request.state` and returns it; removed after each test.
  Tests marked `real_auth` skip the override: all of `test_api_key.py` and three
  other tests. The pinned values stay `APP_ENV=test` with a key, auth on. No
  pre-existing test was edited. The override only exists on `app.main.app`;
  apps built by `create_app()` in tests use the real dependency.
- **D7. The fixture pins all 44 settings, not three.** Every value comes from a
  build with no `.env`. Environment variables of the process still apply (that
  is how CI sets `LLM_PROVIDER=none`), so a variable exported in a developer's
  shell is not neutralised; only the `.env` file is.
- **D8. `app/config.py` reads no `.env` when `PYTEST_VERSION` is set.**
  Production code that knows about the test runner; this is the one I would
  look at hardest. Why: three objects are built from the settings while modules
  are imported (section 10, item 5), before any fixture can run. With the
  fixture alone, a developer `.env` with `CORTEX_REDIS_FALLBACK_ENABLED=false`
  failed 6 tests, and one with `REDIS_URL` or `DATABASE_URL` pointing at real
  services would send the offline suite to them. pytest sets `PYTEST_VERSION`
  for the whole session before it imports any conftest
  (`_pytest/config/__init__.py`, `main()`), and child interpreters inherit it.
  Safe direction: not reading the file can only remove configuration, and with
  nothing configured auth refuses to start. Alternatives: two lines at the top
  of `tests/conftest.py` setting an explicit switch (more than "one fixture");
  or lazy construction in the two read-only files (the proper fix).
- **D9. `live_llm` tests still read the developer's `.env`**, with the same
  values pinned on top (tracing off included).
- **D10. Frozen means "every normal way of modifying raises"**, not a sandbox:
  `dict.__setitem__(frozen, k, v)` on the base class bypasses it (the store
  itself uses that in one place, `_add_payload`). `copy.deepcopy` and pickle
  return plain data. Derived caches inside connectors (`lru_cache`, 20
  connectors) are plain lists built by the connectors: their records are frozen,
  the list around them is not, and connectors are read-only for this task.
- **D11. `/ready` log masking is literal.** A password that is also an ordinary
  word is masked wherever the word appears. With the compose default
  (`cortex:cortex`), a logged file name such as `cortex_air_block_c_dataset.json`
  shows as `***_air_block_c_dataset.json` in that one log line. The load error
  itself is logged unmasked by `layer2.data_load_failed`.
- **D12. Docs exposure is decided when the app object is built** (import), not
  per request.

## 13. Owner actions

1. **Your local `.env` must now say how you want auth, or the app will not
   serve.** One of:
   - `APP_ENV=local` and `CORTEX_AUTH_DISABLED=true`: no authentication, local
     development only.
   - `APP_ENV=local` (or anything else) and a real key hash in
     `CORTEX_API_KEYS_JSON`; clients send `X-API-Key`.

   With `APP_ENV=local` and neither of the two, the app starts and every
   `/api/v1` call answers 401. With no `APP_ENV` and no keys it refuses to
   start. I did not read your `.env`, so I do not know which case applies.
2. `/docs` is only there with `APP_ENV=local`.
3. Postman collection and `scripts/destructive_layer1_runner.py`: add the
   `X-API-Key` header, or run the server in the disabled mode above.
4. Decide the `/ready` response code (section 10, item 1) and D5, D8.
5. Update `README.md:104-106` (section 10, item 2).
6. Push `wave-0-fu` and check the CI run.
7. Still open from W0: `LANGSMITH_TRACING=true` in the local `.env` applies when
   the app runs (tests are now protected from it; the running app is not).
