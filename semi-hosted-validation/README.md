# Semi currency zero-option candidate: frozen native comparison

This branch is a validation experiment, not an upstream product PR. It compares
the unchanged Semi release source with a two-line nullish-fallback candidate.
Mixed min/max/precision precedence is unresolved: recorded candidate exceptions
must not be described as automatically correct or backward compatible.

## Frozen source and proposed product change

Public source is DouyinFE/semi-design at
`2a4ec36222a439098e8aaea236518c66752854ca`, original tree
`f1173f8221e35ec3dcd439b91be8a9f796972567`, 3,975 tracked files.
The candidate changes only InputNumberFoundation.formatCurrency's two option
expressions from `explicit || precision || undefined` to
`explicit ?? precision ?? undefined`. It preserves zero and null/undefined
fallback without adding clamps, catches, parser changes or dependencies.

Proposed product files are the Foundation source plus two new native test files:
18 direct Foundation cases and seven mounted InputNumber cases. Original seven
RED test identities remain included. The component tests cover initialization,
blur after input, controlled updates, explicit valid bounds, defaults, positive
precision and noncurrency zero precision. Candidate expectations are hypotheses
until the original native runner actually produces results.

The separate compatibility-probe test is diagnostic-only and is not part of the
proposed product patch. It records six actual results or exceptions from the real
Foundation without asserting a new public API contract. In particular,
precision=2/max=0 and precision=0/min=1 must be reviewed against maintainers'
intended precedence before any publication claim of compatibility.

## Execution scope

Only push on `dvd233/flowgram.ai`'s
`verify/semi-currency-native-20261007` runs this workflow. Repository ID
1352889832, owner ID 111864431, public visibility, event/ref/SHA and hosted
Linux/X64 are checked. The declared sole parent is
`84940446d2f0e640a5c3edabb60180e08cdc3c8d`. The payload consists of seven exact
regular files; it does not inherit unrelated parent-tree files or workflows.
No default branch or existing contribution PR is changed.

Both checkouts disable persisted credentials. Workflow token permission is
contents:read. Node24.19.0, verified official Yarn1.22.22, original frozen lock
and script-disabled whole-workspace installation are unchanged from V2. npm is
the unmodified version supplied with Node and its actual version is recorded.
No dependency upgrades, registry/proxy/TLS changes, package cache restores,
Corepack/global installs, application server, browser, Cypress, real model/API
call, bootstrap, deployment or publishing step is included.

The one explicit preparation script added in this revision is the repository's
existing ESLint workspace build: `npm run build:lib
--workspace=eslint-plugin-semi-design`. Its original script is `rimraf lib && tsc`.
The generated lib directory must initially be absent. Its three reviewed source
modules and original tsconfig are unchanged; exactly three generated JavaScript
files are allowed and subsequently hashed. Dependency lifecycle hooks remain
disabled. This is an explicit local-tool compile, not permission to run all
workspace builds or install scripts.

After the frozen installation, the job runs:

1. The complete original InputNumber test file with unchanged production.
2. The two new product test files on original production: expected 25 assertions,
   eleven precise formatting failures and fourteen controls, no skips/todos.
3. The separate original mixed-option diagnostic probe.
4. The exact two-line candidate, then the same 25 cases expecting GREEN, the
   complete original InputNumber file and the candidate mixed-option probe.
5. Revert only production and rerun the same 25 cases for the negative control;
   restore only the candidate and rerun them for restored GREEN.
6. Compile the original ESLint workspace tool. Run original root no-emit
   TypeScript and full script lint on reverted production, then restore the
   candidate and repeat both checks. Finally lint all three product files with
   --no-ignore. No production changes happen except the same verified revert and
   restore operations. Source hashes are checked after every command.

All test phases use the original npm test:unit/Jest24/Babel/jsdom/Enzyme setup.
No reporters or transforms are substituted. Original-file inventories, candidate
delta, generated plugin files and complete logical validation trees are checked
throughout. Only one original source file may differ when the candidate is active;
reverted phases must match every original blob. The diagnostic probe is explicitly
included in validation tree inventories but excluded from the product patch.

The root type check invokes installed TypeScript with original root tsconfig and
`--noEmit --pretty false --incremental false`. Full script lint is the original
`npm run lint:script`, with native JSON output directed to evidence. Tests are
ignored by that script's existing eslintignore, so the three product files also
receive an explicit `--no-ignore` lint check. No automatic --fix runs.

## Interpreting evidence

Raw native exit codes, logs and Jest/ESLint JSON are retained. Scoped verification
requires original RED, candidate GREEN, production-only negative RED, restored
GREEN, passing unchanged original InputNumber tests with identical case identities,
and changed-file lint success. Missing/extra cases, wrong matcher messages, skips,
runtime/setup errors, drift, interruption or incomplete artifacts fail that gate.

The workflow wrapper can succeed for this scoped proof while broader root checks
have pre-existing failures. Their real exits are separately recorded, and
all_requested_checks_passed is true only if every requested broader check passes.
Do not infer root type/lint success from the workflow badge. No full UI test suite,
browser test, build of all packages, full upstream CI or ready-to-merge claim is
made. Mixed compatibility always remains marked for review in this experiment.

The diagnostic probe's single passing Jest wrapper is not a product regression
pass and is not included in the 25-case counts. Read its actual value/error records.

## Resource, network and evidence bounds

One standard ubuntu-24.04 job, maximum 60 minutes; orchestration has a shared
55-minute execution deadline so evidence cleanup has headroom. Install retains
the 30-minute cap. Individual native/root checks have bounded limits recorded in
the manifest. No automatic retries or alternative execution route exists.
Source/dependency/cache sampling retains the 7 GiB soft stop and 4 GiB free-space
floor. This is a sampled bound, not a filesystem quota.

Public downloads are GitHub Actions/source/Node infrastructure and official npm/
Yarn registries from the frozen lock. Artifacts go only to GitHub Actions storage.
Child processes receive a fresh HOME/cache and an explicit environment without
user secrets/GitHub tokens. The diagnostic phase adds one controlled output path
under its evidence directory. The harness is not an egress firewall; it does not
claim every third-party runtime instruction was audited.

Only allowlisted regular nonsymlink evidence is uploaded: maximum 8 MiB per file,
48 MiB expanded and 12,000,000 compressed bytes, retention 3 days. The increase from
V2 accommodates the extra complete source snapshots and original test/root-check
reports. Source/dependency/cache trees and credentials remain excluded. Overflow
or truncation marks evidence incomplete and prevents scoped validation success.
Account billing/quota state is not asserted; no paid runner tier is requested.

## Preserved history

[V1](https://github.com/dvd233/flowgram.ai/actions/runs/37692668357) stopped before
the verified Yarn installer because a manifest expanded-size constant was wrong.
[V2](https://github.com/dvd233/flowgram.ai/actions/runs/37693839026) corrected only
that harness metadata and obtained actual original-code four-failed/three-passed
Jest evidence. Its native npm exit was 1; no production fix had been applied.
Both historical outcomes remain intact. This revision must establish its own
expanded native results rather than reuse those seven counts as a GREEN result.
