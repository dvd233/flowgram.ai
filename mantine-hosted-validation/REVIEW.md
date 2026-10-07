# Mantine native validation method

This standalone validation payload tests a frozen Mantine change in an isolated branch. A Mantine source PR has not been created. This document describes the method and known prior evidence; it does not claim that the hosted jobs or all upstream CI have passed.

## Repository and commit layout

The workflow runs only on pushes to `dvd233/flowgram.ai`, branch `verify/mantine-leading-native-20261007`. It checks immutable repository ID `1352889832`, owner ID `111864431`, the exact branch ref, push event and nondeleted event payload. `event.after`, `GITHUB_SHA`, checked-out payload HEAD and `GITHUB_WORKFLOW_SHA` must agree. Only GitHub-hosted Linux/X64 runners are accepted. There are no pull-request or manual triggers.

This revision has sole parent `e0f533596fbb3aff8a96d36860a3dbb628c4a504`, the previously published Mantine validation payload. It fast-forwards the same verification branch. Its current tree is constructed independently from empty and contains exactly five files:

- `.github/workflows/mantine-frozen-native.yml`
- `mantine-hosted-validation/run.py`
- `mantine-hosted-validation/manifest.json`
- `mantine-hosted-validation/candidate.patch`
- `mantine-hosted-validation/REVIEW.md`

The parent does not supply files to this current tree. Consequently, inherited Flowgram application code and workflows are absent. Default/source branches and existing PRs are unchanged. Advancing the verification branch triggers the two jobs. The runner rejects any additional tracked payload paths and records the actual payload commit, tree and file hashes. Consumers must match those values to the exact published commit and workflow run, including its attempt number.

## Frozen source identity

Both lanes separately check out [mantinedev/mantine at f0e2bc6d28b0f0b8aaefb5b859937bf77f539df7](https://github.com/mantinedev/mantine/tree/f0e2bc6d28b0f0b8aaefb5b859937bf77f539df7).

- Base whole tree: `1a1b1b2a22a7d34b0417adf59541e5380912bcd5`
- Candidate whole tree: `f09fcc82915954996ad4f04345bec2beda2845ed`
- Negative-control whole tree: `f5a8cccb082e2e4464223247c14c98bfb8d3b3b9`
- Original lock SHA-256: `6098f86637b773b7ff5dea4e8353de2ab3b4fe7d4edfeb6f155fd344090e38a7`
- Patch SHA-256: `ca7f7a1d4d6346aaa8da76a8630da7b3ddcf4541e7958cd75b80909b522738a0`
- Candidate production source SHA-256: `02002e0926318f9b0fd065ed6aef8ed7d790c1df08ba75114632819b924a2696`
- Candidate test SHA-256: `bbe0fc10f8d0aae1e719909b555099ed41d64990edc80af1891ce00c4256411c`

The target paths and original/candidate Git blob IDs are in manifest.json. The candidate tree is the complete original tree with precisely two replaced blobs. The negative tree replaces only the test blob. Re-encoding the complete, untruncated 8,130-entry upstream tree reproduced its original tree ID before these substitutions. Hosted Git independently verifies each resulting tree through `git write-tree`, together with working tracked files, target hashes, package.json and the original lock, before and after each command.

Generated ignored compiler outputs are allowed; they are never committed to the source index or uploaded as evidence. `final_source_commit` remains null. Evidence for this base-plus-patch tree must not be attributed to a later source commit unless its complete tree matches, or every additional path-level difference is explicitly accounted for. Matching only the two target files is insufficient.

## Tooling, lifecycle and execution scope

- Node is exactly 26.3.0, matching the original .nvmrc. No project dependency/build/test cache is restored. Each lane has an independent clean hosted runner.
- Yarn is exactly 4.18.0. The official standalone CLI is downloaded from `https://repo.yarnpkg.com/4.18.0/packages/yarnpkg-cli/bin/yarn.js` and checked against SHA-256 `fb8b1d20be72a0b544a35bcec4c7ed0ff55a9b173c01f191b02ba164b2051db5` before execution. These bytes match the previously Corepack-verified release. Direct CLI bootstrap is a disclosed difference from invoking Corepack.
- Installation is `node <verified-yarn.js> install --immutable --mode=skip-build`, with unchanged manifest/lock, fresh per-lane cache, `YARN_ENABLE_SCRIPTS=false` and telemetry disabled. Dependency lifecycle hooks do not execute. This differs from the original enableScripts=true configuration and is recorded in each receipt. Missing required binaries fail instead of enabling lifecycle hooks or replacing dependencies.
- `npm_config_ignore_scripts=true` suppresses automatic pre/post hooks but permits explicitly named npm scripts. The runner first inspects every selected root script and both nested app typecheck scripts. If an existing pre/post hook would be suppressed, execution stops with a recorded failure. The frozen manifests contain no affected hooks. The docs app's unrelated postbuild=next-sitemap is never selected.
- Native checks retain npm offline=true, yes=false and update_notifier=false, with a fresh npm cache. Yarn networking is disabled after installation. Missing tools must fail rather than trigger package downloads. These settings are not an OS network firewall.
- Normal compiler/test subprocesses and internal IPC, including the unchanged tsx Unix socket and esbuild communication, are part of hosted execution. No local loader, temporary-directory, permission or security workaround is used for the earlier local IPC denial.
- No application server, live browser, application/model API test, docs setup/sponsors request, release, deployment or package publication is run. No API secrets, GH_TOKEN, environment secrets or OIDC are configured. Child commands receive an allowlisted environment and empty HOME without Actions runtime tokens.
- Permissions are only `contents: read`. Both checkouts use persist-credentials=false, no submodules and no LFS. The official artifact action uses its ordinary runner-provided upload channel.

The pinned official actions were checked against their tag objects on 2026-10-07:

- [checkout v4.2.2](https://api.github.com/repos/actions/checkout/git/ref/tags/v4.2.2): `11bd71901bbe5b1630ceea73d27597364c9af683`
- [setup-node v4.4.0](https://api.github.com/repos/actions/setup-node/git/ref/tags/v4.4.0): `49933ea5288caeca8642d1e84afbd3f7d6820020`
- [upload-artifact v4.6.2](https://api.github.com/repos/actions/upload-artifact/git/ref/tags/v4.6.2): `ea165f8d65b6e75b540449e92b4886f43607fa02`

These are pinned stable releases, not a claim to use the latest versions. Action/runtime warnings remain in GitHub job logs; the Python receipts capture commands supervised by the runner.

## Original commands and lane order

Both lanes run these original project entrypoints in order:

1. `npm run build`
2. `npm run typecheck`
3. `npm run jest -- --runInBand --runTestsByPath <target-test> --json --outputFile=<report>`
4. `npm run jest -- --runInBand packages/@mantine/hooks --json --outputFile=<report>`
5. `npx oxlint -c oxlint.config.mjs <two-target-files>`
6. `npm run lint`
7. `npm run format:write:files -- <two-target-files>`

Original build is tsx scripts/build: dependency-ordered declarations via npx tsc, rolldown ESM/CJS compilation, original PostCSS/banner processing and CSS generation. It discovers 30 nonprivate packages, including @docs/demos and @docs/styles-api. It compiles package source without launching the compiled applications. Build outputs and .buildinfo.json must initially be absent, and a successful build must report packages built with zero unchanged-cache skips.

Original typecheck retains its root tsc → docs app tsc → help app tsc && chain. Original lint retains oxlint → stylelint. Jest uses the original configuration, esbuild-jest, jsdom and setup files. There is no substitute loader, transformer, test implementation or synthetic result. Formatting that changes a frozen byte fails source identity.

Base target must pass all 36 tests. Candidate target must pass all 43. Both full-hooks results must contain exactly all 64 frozen suite paths, checked against the manifest's path digest. Candidate then restores only the exact base production blob, retaining the frozen new tests, and runs a native negative target. Finally it restores only the frozen production hunk and reruns the 43-test target.

The negative requires exit 1, one failed suite, exactly four fixed assertion fullName values, 39 passes out of 43, and no runtime error/interruption/skip/todo. Each failed assertion must contain exactly one native matcherResult with pass=false and this exact message, also present in its single failureMessages entry:

expect(jest.fn()).toHaveBeenCalledTimes(expected)

Expected number of calls: 1
Received number of calls: 2

Counts without that matcher evidence do not qualify. Original exit=1 remains in the receipt; a separate field records whether the expected negative matched.

All 64 frozen hooks test files were inspected for explicit skip/todo/focus and xit/fit-family markers; none was present. The manifest records the pattern and empty inventory, and hosted preflight repeats the check. Unexpected source markers require review. Native pending/todo/nonpassing assertions are recorded, and unexpected runtime skips fail completeness. The source scan does not claim to prove arbitrary dynamic behavior. The two full-hook suite sets must agree, with candidate assertion count exceeding base by seven.

Independent checks continue after build/typecheck failures, preserving original exits and logs. Installation or source-integrity failure blocks dependent stages and reports them as unreached. A failed command is not converted to success. Both lane conclusions and receipts are required; this subset is not all upstream CI.

## Evidence limits

Receipts bind repository/owner IDs, ref/event, payload/workflow commit and tree, run ID/attempt, lane, source tree, target/lock hashes, exact arguments, exit codes, elapsed times, warnings/skips and native Jest JSON.

Only known receipt/JSON/log filenames directly in the results directory are eligible for upload; every entry must be a regular nonsymlink file. Directories, unknown files and source/dependency outputs are excluded. Per lane: expanded file ≤8,000,000 bytes, expanded total ≤24,000,000 bytes, gzip tarball ≤4,500,000 bytes. Two-lane limits are 48,000,000 expanded bytes and 9,000,000 compressed payload bytes, leaving ZIP-envelope headroom below 10 MB. Verify actual GitHub archive sizes before downloading or distributing them.

An allowlist/type/size violation fails the lane and uploads only a bounded failure receipt that lists omissions. It never represents complete evidence. Large logs are not read without bounds; hashing is streamed. Artifacts retain no source files, dependency tree, compiler outputs or environment dump. The already-compressed tarball is uploaded with artifact compression=0 and three-day retention.

## Known prior evidence and remaining limitations

Prior local native runs recorded 43 candidate target passes; exact original-production negative 4 fail/39 pass; and changed-file lint/format exit 0. Original build exited 1 because tsx Unix IPC listen was denied with EPERM. No workaround was attempted. Original typecheck exited 2 in the docs app; root tsc passed and help-app tsc was not reached. A terminal full-hooks result was not available in that prior evidence. Hosted results remain separate and must be verified for their exact run/commit.

The docs failure contained 238 TS2307 errors for 17 workspace packages whose exports point to generated lib declarations. Original build covers these packages and may resolve those errors; success is not assumed. Docs already declares module '*.json', and the log did not show missing .docgen JSON modules. No placeholder files or weakened type checks are introduced.

Original setup remains excluded. It runs docs:docgen, docs:sponsors and generate-css-exports. Docgen/count parse local sources and write ignored JSON. CSS export generation is local and already performed by original build. Sponsors makes an unauthenticated read of `https://opencollective.com/mantinedev/members/all.json`, filters public backer data using the current date and writes ignored JSON. That is public data preparation, distinct from a live model/application test, but it is additional network activity and is not performed here. No sponsor token or API secret is used. Those generated JSON files are not established prerequisites for the observed typecheck failure. If original checks still fail, their failures remain visible rather than being hidden by substitute setup or stubs.


## Revision 2: installed-package inventory correction

[Hosted attempt 37675364642](https://github.com/dvd233/flowgram.ai/actions/runs/37675364642) used payload commit `e0f533596fbb3aff8a96d36860a3dbb628c4a504`. Both lanes completed Node/npm/Yarn version checks, the verified Yarn download and immutable installation with exit 0. Both then failed in the validation harness's package inventory with JSONDecodeError at line 2, column 1. Build, typecheck and native tests had not started. The traceback did not record the failing filename, so it does not establish which hosted file caused the error.

A read-only examination of an existing same-lock local installation found a matching malformed test fixture: `node_modules/react-docgen/node_modules/resolve/test/resolver/malformed_package_json/package.json`, containing only an opening brace and newline. It was the only invalid JSON among 2,911 recursively found package.json files. This is local same-kind diagnostic evidence, not a claim that the hosted traceback directly identified that pathname.

The previous recursive inventory incorrectly treated arbitrary package-internal fixtures and documentation as installed package manifests. Revision 2 instead reads the JSON package-location map generated by the pinned Yarn 4.18.0 node-modules linker: `node_modules/.package-map.json`, `packages[*].url`. The pinned Yarn CLI's writer was inspected without executing it. In the existing local installation, the map's 2,328 unique roots exactly matched all resolved locations in Yarn's separate .yarn-state.yml: 2,295 dependency roots and 33 workspace/project roots. Every mapped root manifest parsed successfully; eight actual install-hook entries were found. These counts are diagnostic observations, not hardcoded hosted assumptions.

The corrected inventory validates the map schema and relative locations, keeps resolved roots within the source checkout, explicitly accounts for workspace/project roots, rejects external root/manifest links, and parses every unique mapped root's package.json strictly. An absent map, unsupported location, missing manifest, malformed root JSON or invalid manifest shape fails with the relative manifest/locator path. There is no catch-and-skip fallback. Duplicate mapped aliases share one strictly checked physical root. The receipt records completion only after every declared unique root was processed, together with the map hash, root counts, a digest of root paths and manifest hashes, and the selected preinstall/install/postinstall hooks. Lifecycle execution remains disabled.

Exclusion is based on map membership: package-internal fixture, documentation and other package.json files that are not declared installed roots are outside this inventory. The inventory does not claim to audit every JSON file shipped inside dependencies. A malformed actual mapped root is still a hard failure. Runtime manifests and workspace data remain read-only.

Six standalone standard-library Python helper checks passed against the existing read-only installation and temporary helper-owned fixtures: actual root counts/hooks, fixture exclusion with workspace inclusion, malformed dependency/workspace root rejection, external-location and symlink rejection, missing-root-manifest failure, and invalid-map failure without fallback. These are validation-harness regression checks, not Mantine native project tests. No third-party project code, installation or network request was executed for those checks. Revision 2 changes only the inventory helper and this explanation; the workflow, frozen manifest, source patch, commands, negative-control gate, permissions and execution scope remain unchanged.
