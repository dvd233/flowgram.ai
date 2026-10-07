# Semi InputNumber currency: original-source RED verification

This independent validation branch contains only this public method note, a
workflow, a Python orchestration script, a manifest and one proposed test file.
It contains no production fix. A successful validation job means that the
original implementation failed exactly four zero-option assertions while three
controls passed. It does not mean that the unit tests passed, that the component
UI works, or that Semi is green. The original npm exit code and native Jest JSON
remain unchanged in the evidence.

## Frozen inputs

- Public source: `DouyinFE/semi-design`
- Commit: `2a4ec36222a439098e8aaea236518c66752854ca`
- Complete original Git tree: `f1173f8221e35ec3dcd439b91be8a9f796972567`
- Original tracked file count: 3,975
- Source plus only the proposed test: `fdbbe866554d65b76f135b5fe796c70b161c6e49`
- Node: `24.19.0`; Yarn: `1.22.22`; npm is the unmodified version supplied by
  the selected setup-node runtime, with actual version and paths recorded
- Original lock SHA-256:
  `0f2c6320fd31d00d50cd4796f1c1ac2d2e056921b37bbcb58f9f662288aa162c`

The manifest binds the proposed test bytes, seven assertion identities, original
lock and package-manager declaration, source tree, exact Node/Yarn versions and
official action commit pins. Checkout tokens have read-only content permission
and are not persisted into either checkout. The test process receives a fresh
HOME and a bounded environment without GitHub tokens or repository secrets.
No project dependency cache is restored.

The validation branch's parent is
`387e748b00a864779bcc617114fb277cb299bcc3`; this only provides a Git parent link.
The runner checks the exact sole parent from the raw commit header, including
in shallow checkouts. The new tree is independently constructed from five files. Parent-tree project
files and workflows are not part of the validation tree. This branch does not
change the host repository's default branch or create an upstream pull request.

## Native procedure

1. Require the exact public execution repository `dvd233/flowgram.ai`, repository
   ID `1352889832`, owner ID `111864431`, push event, branch
   `verify/semi-currency-native-20261007` and GitHub-hosted Linux X64 runner.
   Bind checkout HEAD, workflow SHA and push-event SHA to one immutable commit.
2. Checkout the exact upstream commit. Reconstruct the complete tree from its
   index and independently hash every original regular file and executable mode.
3. Fetch only the official Yarn 1.22.22 npm tarball. Require its previously
   established byte count and SHA-512 SRI, which also matches the original
   `packageManager` declaration. Extract only the eleven expected regular files.
   Do not execute Yarn's preinstall hook, activate Corepack, or install globally.
4. Run the original full workspace dependency installation:
   `node <verified-yarn.js> install --frozen-lockfile --ignore-scripts --non-interactive --cache-folder <isolated-cache>`
   All lifecycle scripts remain disabled. All 3,735 frozen lock entries have
   integrity metadata and resolve over HTTPS to the official npm or Yarn
   registries. Original metadata and lockfiles are not edited.
5. Require the exact root React16, Jest24, babel-jest24, Enzyme, Cheerio and lodash
   versions recorded in the manifest. This is a focused metadata check, not a
   claim that every transitive package has been behaviorally audited.
6. Add only
   `packages/semi-ui/inputNumber/__test__/InputNumber.currency.test.js`.
   Keep the original Jest, Babel, jsdom and Enzyme setup untouched, then run:
   `npm run test:unit -- --runInBand --watch=false --notify=false --runTestsByPath packages/semi-ui/inputNumber/__test__/InputNumber.currency.test.js --json --outputFile=<evidence>/foundation-red.json`
7. Independently rehash all original source files and the new test before and
   after execution. Preserve initial, post-install and final original-file
   inventories. Never patch production to force a predicted result.

The test imports the real InputNumberFoundation and uses its normal adapter
interface. It calls `formatCurrency` directly with USD, en-US, symbol display and
`showCurrencySymbol=true`; it neither copies the algorithm nor mocks Intl.
The production import chain is nine local Foundation files plus lodash. The
original repository test setup also executes. No InputNumber mount, UI barrel
suite, application server, browser, Cypress, build, bootstrap, sponsor script,
application/model API, deployment or publishing step is included.

## Expected result is a hypothesis until observed

The three controls expect default USD digits (`$12.00`), positive precision 2
(`$12.30`) and positive minimum 2 / maximum 4 (`$12.3456`). The four proposed
regressions independently set minimum 0, maximum 0, precision 0, or both minimum
and maximum 0. Each expects `$12` for input 12. Static review predicts that the
original source instead returns `$12.00`; that prediction is not measured proof.

The evidence gate requires the actual npm exit code 1, exactly one native suite,
all seven exact assertion names, four failed assertions, three passing controls,
no pending assertions and four native `toBe` failure texts with exact Expected
`"$12"` and Received `"$12.00"` lines. Native JSON is never rewritten. Optional
Jest-version-specific fields are checked when present and their absence is
recorded explicitly. The gate does not require Jest30 matcher metadata or swap
reporters or transforms. Missing JSON, different failures, incomplete counts,
setup/import errors, timeouts and unexpected results are reported as unvalidated.
The separate maximum=0 plus precision=2 conflict is outside this test.

## Budgets, stops and evidence

The workflow is capped at 45 minutes, installation at 30 minutes and the one
test command at 5 minutes. A conservative 7 GiB sampled soft stop reserves room
within an 8 GiB source/dependency/cache budget; at least 4 GiB free disk is
required. Sampling is not a hard filesystem quota. Logs have an 8 MiB cap;
reaching it stops the process, records any truncation and prevents a complete
validation claim. No automatic retry or alternate install/test route exists.

Authentication/access, integrity, engine, lock or TLS errors stop the lane.
Networking uses the hosted runner's ordinary HTTPS trust. TLS verification is
not disabled, trust stores and proxies are not modified, and no local framework
network settings are copied. If the exact Node/Yarn runtime is unavailable, the
job stops rather than upgrading or replacing tools silently. npm is used as
provided by setup-node and its actual version/executable paths are recorded;
the separately observed npm 11.9.0 is a reference, not a project version pin or
an assumption about the official Node bundle. npm is not upgraded or replaced.

Artifacts contain only allowlisted regular nonsymlink receipts, public command
logs, source hash inventories, dependency version metadata and native Jest JSON.
No source trees, dependency trees, caches or credentials are uploaded. Limits
are 8 MiB per file, 24 MiB expanded total and 4,500,000 bytes compressed; retention
is three days. If full evidence cannot fit, the job reports incomplete evidence
and uploads a bounded receipt that identifies omissions. It cannot report a
complete successful verification without complete evidence. Raw command exit
codes are retained, including the expected failing npm test exit code.

This harness alone does not establish contribution readiness: a repair, positive
regression run, appropriate UI/integration checks, broader repository checks and
a fresh duplicate/ownership review would be separate work.

## Preserved first attempt

[The first attempt](https://github.com/dvd233/flowgram.ai/actions/runs/37692668357)
stopped before running the verified Yarn installer or native test because the manifest recorded an
incorrect expanded Yarn size (5,350,912 bytes). The unchanged official archive
and npm metadata both give 5,340,487 bytes across the same eleven files. This
revision corrects that constant and advances the declared parent; source, tests,
runtime pins, security checks, installation commands and result gates are unchanged.
The first attempt is a harness failure and provides no native RED result.
