# Mantine native validation, revision 3

This standalone payload validates an exact Mantine base-plus-patch tree. No Mantine source PR has been created. Both revision 2 lanes passed build, focused/full-hooks tests, lint and formatting; the candidate lane additionally matched the negative control and passed the restored target. The sole remaining failure was the same docs typecheck error in both lanes. Revision 3 adds only the original public sponsor-data preparation needed to investigate that failure. It does not claim that revision 3 or all upstream CI has passed.

## Isolated publication and run identity

Only pushes to `dvd233/flowgram.ai`, branch `verify/mantine-leading-native-20261007`, trigger this workflow. Guards require repository ID `1352889832`, owner ID `111864431`, the exact branch ref, a nondeleted push, and GitHub-hosted Linux/X64 runners. event.after, GITHUB_SHA, payload HEAD and GITHUB_WORKFLOW_SHA must agree.

Revision 3's sole parent is revision 2 commit `251841c5423ae199f422b9c5e117e30d4b92c7ac`; publication fast-forwards the same verification branch. Its current tree is still constructed from empty and contains exactly:

- `.github/workflows/mantine-frozen-native.yml`
- `mantine-hosted-validation/run.py`
- `mantine-hosted-validation/manifest.json`
- `mantine-hosted-validation/candidate.patch`
- `mantine-hosted-validation/REVIEW.md`

No inherited Flowgram application files/workflows, default/source-branch edits or PR changes are included. The runner verifies this exact path set and records the payload commit/tree/file hashes. Results must be matched to that commit and the exact run ID/attempt.

## Frozen Mantine input

Both lanes independently check out [mantinedev/mantine at f0e2bc6d28b0f0b8aaefb5b859937bf77f539df7](https://github.com/mantinedev/mantine/tree/f0e2bc6d28b0f0b8aaefb5b859937bf77f539df7).

- Base tree: `1a1b1b2a22a7d34b0417adf59541e5380912bcd5`
- Candidate tree: `f09fcc82915954996ad4f04345bec2beda2845ed`
- Negative-control tree: `f5a8cccb082e2e4464223247c14c98bfb8d3b3b9`
- Lock SHA-256: `6098f86637b773b7ff5dea4e8353de2ab3b4fe7d4edfeb6f155fd344090e38a7`
- Patch SHA-256: `ca7f7a1d4d6346aaa8da76a8630da7b3ddcf4541e7958cd75b80909b522738a0`
- Candidate source SHA-256: `02002e0926318f9b0fd065ed6aef8ed7d790c1df08ba75114632819b924a2696`
- Candidate test SHA-256: `bbe0fc10f8d0aae1e719909b555099ed41d64990edc80af1891ce00c4256411c`

Manifest.json supplies target paths and original/candidate blobs. The original untruncated 8,130-entry tree was re-encoded to reproduce its Git tree ID. Substituting exactly the two target blobs yields the candidate; substituting only the test blob yields the negative tree. Hosted Git independently verifies write-tree, working tracked files, targets, package.json and lock before/after commands. Ignored generated files do not enter the source index.

`final_source_commit` remains null. A later source commit may use these results only after its whole tree matches, or additional path-level differences are explicitly accounted for. Two matching target files alone are insufficient.

## Runtime and scope

Node is exactly 26.3.0 and Yarn exactly 4.18.0. The official standalone Yarn CLI is downloaded from `https://repo.yarnpkg.com/4.18.0/packages/yarnpkg-cli/bin/yarn.js` and checked before execution against SHA-256 `fb8b1d20be72a0b544a35bcec4c7ed0ff55a9b173c01f191b02ba164b2051db5`. Its bytes match the previously Corepack-verified release; direct CLI bootstrap is a disclosed difference.

Installation is `node <verified-yarn.js> install --immutable --mode=skip-build`, with unchanged manifest/lock, fresh lane-specific cache and `YARN_ENABLE_SCRIPTS=false`. Dependency lifecycle hooks remain disabled, a disclosed difference from original enableScripts=true. Missing tools fail instead of enabling hooks, rebuilding dependencies or acquiring replacements. No project caches are restored.

The inventory reads actual package roots from Yarn's generated node_modules/.package-map.json, not every package.json inside package contents. Each unique mapped root, including workspace/project roots, is strictly parsed. Unsupported paths, external links, malformed maps and missing/invalid actual manifests fail with a relative location. Package-internal fixtures not declared installed roots are outside this inventory. Receipts bind the map hash, root counts and a digest of roots/manifest hashes. Six standalone Python helper checks previously passed; these were harness checks, not Mantine tests.

npm ignore_scripts=true still allows explicitly named scripts. Before execution, the runner rejects any existing pre/post hook that would otherwise be suppressed for selected root commands, now including docs:sponsors, and both nested app typechecks. The frozen manifests contain no affected hooks. npm offline=true, yes=false and update_notifier=false remain in effect for native checks; Yarn networking is disabled after install. Those settings prevent missing-package acquisition and are not an OS firewall.

Normal hosted compiler/test subprocesses and internal IPC, including unmodified tsx Unix IPC and esbuild communication, are in scope. No local loader, temporary-directory, permission or security workaround is used for the earlier local IPC denial. No application server, browser, model/application API test, donor-link fetching, broader docs setup, release, deployment or package publication is run.

The sole additional network operation is the original docs:sponsors script's unauthenticated fetch of public build input, described below. No account data, token or custom authentication header is supplied. No GH_TOKEN, secrets, environment secrets or OIDC are configured. Permissions are contents:read only; both checkouts use persist-credentials=false, no submodules and no LFS. Native child commands receive an allowlisted environment and empty HOME without Actions runtime tokens.

Official action tag objects were verified on 2026-10-07:
- [checkout v4.2.2](https://api.github.com/repos/actions/checkout/git/ref/tags/v4.2.2): `11bd71901bbe5b1630ceea73d27597364c9af683`
- [setup-node v4.4.0](https://api.github.com/repos/actions/setup-node/git/ref/tags/v4.4.0): `49933ea5288caeca8642d1e84afbd3f7d6820020`
- [upload-artifact v4.6.2](https://api.github.com/repos/actions/upload-artifact/git/ref/tags/v4.6.2): `ea165f8d65b6e75b540449e92b4886f43607fa02`

These are pinned releases, not a claim to be latest. Action/runtime warnings remain in GitHub job logs.

## Original command sequence

Each independent lane runs:

1. `npm run build`
2. `npm run docs:sponsors`
3. `npm run typecheck`
4. `npm run jest -- --runInBand --runTestsByPath <target-test> --json --outputFile=<report>`
5. `npm run jest -- --runInBand packages/@mantine/hooks --json --outputFile=<report>`
6. `npx oxlint -c oxlint.config.mjs <two-target-files>`
7. `npm run lint`
8. `npm run format:write:files -- <two-target-files>`

Original build retains tsx scripts/build, per-package npx tsc declarations, rolldown ESM/CJS bundling and original PostCSS/CSS generation. Its 30 package outputs and .buildinfo.json must initially be absent; success must report packages built with zero unchanged-cache skips. Original typecheck retains root tsc → docs app tsc → help app tsc through its original && chain. Original lint retains oxlint → stylelint. Jest uses original config, esbuild-jest, jsdom and setup files. No source, type annotation, loader, transformer or fixture is substituted. A formatting change to frozen bytes fails identity.

Base target requires 36 passes; candidate requires 43. Full hooks must cover exactly the same 64 frozen suite paths, checked by digest. The source inventory found no explicit skip/todo/focus or xit/fit-family markers; hosted preflight repeats that audit, and native pending/todo/runtime errors are retained and rejected where unexpected.

Candidate then restores only the exact base production blob, keeps the new tests, runs the native negative, restores the frozen production hunk and reruns 43 tests. Negative must exit 1 without timeout, runtime error, interruption, skips or todos; report exactly the four fixed fullName failures and 39 passes out of 43 in one suite; and contain one native matcherResult per failure with pass=false and the exact message:

expect(jest.fn()).toHaveBeenCalledTimes(expected)

Expected number of calls: 1
Received number of calls: 2

That message must also appear in the assertion's single failureMessages entry. A count-only match or crash is not accepted. The actual exit=1 remains recorded separately from the expected-negative verdict.

Independent checks continue after command failures. Install/source-integrity failure blocks dependent stages with explicit unreached records. Both lane conclusions and receipts are needed; this does not reproduce all upstream CI.

## Why sponsors preparation is the minimal addition

[Revision 2 run 37677488278](https://github.com/dvd233/flowgram.ai/actions/runs/37677488278) built 30 packages in each lane. Base full hooks passed 567 assertions; candidate passed 574, both across 64 suites. Candidate focused/restored targets passed 43; the negative matched 4 fail/39 pass and the four real matcher failures. Full lint and changed formatting passed. Both lanes' sole failure was identical: HomePageSponsors.tsx:7:27 TS7006, item implicitly any. Root tsc passed; docs tsc failed; help-app tsc was not reached.

HomePageSponsors imports ../../../.docgen/sponsors.json and immediately calls data.map((item) => ...). The file is absent from the source tree and ignored. Original build generates CSS exports but not sponsors. The existing ambient module '*.json' declaration permits the missing import to be any, leaving the callback parameter without contextual type. Thus real sponsor JSON is the narrow missing build input indicated by both the source and the identical baseline/candidate diagnostic; the post-preparation typecheck must still verify that inference.

Original npm run docs:sponsors is exactly tsx scripts/docs/sponsors. The frozen script has one fetch invocation per lane to `https://opencollective.com/mantinedev/members/all.json`, without headers, tokens, credentials or environment-secret reads. Original fetch may follow normal HTTP redirects; one source invocation does not guarantee one HTTP hop. No URLs contained in backer records are fetched. This is a public read-only build-input request, not a model/application API test. It exposes the runner's ordinary request metadata, not account data.

The script filters public records using the current date and writes ignored apps/mantine.dev/src/.docgen/sponsors.json. It does not change tracked files. The runner verifies the original script hash and initial output absence, then records only generated-file hash, bytes and entry count. A legitimate empty array is reported as count=0 with an empty-input limitation; no records/types are fabricated, and the original typecheck determines project-check success. Failed fetch/script exit or invalid/missing output remains a failure. No stub, type annotation or source change is introduced.

The rest of setup is not justified by this failure and remains excluded. PropsTable already assigns docgenData to an explicitly typed Record<string, Docgen>; count.json is an MDX input; CSS exports are generated by original build. Revision 2 docs tsc reported no other error. Running docs:docgen, docs:count or the broader setup would expand preparation without evidence that it is needed here. Help-app checking must still run through the unchanged full typecheck when docs succeeds.

## Public-input output privacy and artifact limits

The normal sponsors script logs only a saved-count message, but its error logger could expose a response snippet through an exception. Therefore this step's complete original stdout/stderr remains only in the ephemeral runner temporary directory. It is not sent to console, warning excerpts or artifacts. The published controlled status log/receipt contains the exact command, real exit/elapsed time, original-log hash/bytes, raw-log-uploaded=false and generated JSON hash/bytes/count. Raw backer records and donor fields are never uploaded. This deliberately narrower evidence scope is explicit; not every byte of this preparation step's output is publicly retained.

All other original build/typecheck/test/lint/format logs and native Jest JSON remain complete under the existing size limits. Receipts bind repository/owner/ref/event, payload/workflow commit/tree, run ID/attempt, lane, frozen source/lock hashes, arguments, exits, times and evidence status.

Only known receipt/JSON/log filenames directly in the result directory may be packed, and each must be a regular nonsymlink file. Limits per lane: expanded file ≤8,000,000 bytes, expanded total ≤24,000,000 bytes, compressed tarball ≤4,500,000 bytes. Combined lanes are bounded to 48,000,000 expanded bytes and 9,000,000 compressed payload bytes, leaving ZIP-envelope headroom below 10 MB. Verify actual GitHub archive sizes. Source files, dependencies, generated sponsors/build output and environment dumps are excluded.

Any packaging allowlist/type/size violation fails the lane and retains only a bounded failure receipt listing omissions. Logs are read with bounds and hashing is streamed. The gzip artifact uses upload compression=0 and three-day retention. Earlier failed attempts and revision 2's actual results remain distinct evidence; revision 3 does not overwrite or retroactively relabel them.
