# Query-only HLS URI validation

This isolated branch validates a small xgplayer HLS parser correction against fixed upstream commit 65b6a0ec223e3a92d55c4453df419197806967d4. It is a verification payload, not a FlowGram source change or an upstream pull request.

A URI such as ?segment=1.ts currently loses the containing playlist's filename. RFC8216 §4.1 resolves relative URIs against the playlist URI; RFC3986 §5.4.1 explicitly preserves the base path for a query-only reference. The proposed source change handles only the leading question-mark case. Existing absolute, scheme-relative and path-relative behavior is retained.

The payload uses the repository's original Jest configuration, setup, parser test entry and locked toolchain. It records:

1. The four existing parser tests on original source.
2. Seven new regression tests on original source; all seven must fail through assertions, with the four original tests passing.
3. All eleven tests on the candidate.
4. The same seven failures after reverting only production code.
5. All eleven tests again after restoring the candidate.
6. Read-only Biome results on original and candidate production files. Existing lint failures remain visible and prevent a full-pass claim.

The script verifies every original source blob after installation and permits only the two proposed source/test files to differ during comparisons. Candidate checksums, source tree, Yarn tarball digest and exact dependency versions are in manifest.json. Installation uses the original lockfile with lifecycle scripts disabled. No registry rewrite, install retry, browser, media endpoint, application server or credentials are part of this job.

Two earlier dependency-install attempts in the cloud workspace failed with DNS EAI_AGAIN for registry.yarnpkg.com, including one formal network escalation. Those raw setup failures are retained in local-setup-failures.txt. They occurred before native Jest or Biome and do not count as regression failures. Separate offline execution of the five original parser modules established eleven failing URL cases and six passing controls; the candidate passed all seventeen, and the production-only negative control reproduced the failures. That is not a browser playback or full-CI claim.

This job is restricted to one standard GitHub-hosted ubuntu-24.04 runner, a 25-minute job limit, a 20-minute script limit, a 15-minute install limit and a 2 GiB working footprint. It receives contents:read only, persists no checkout credentials, consumes no configured secrets, and uploads only a bounded evidence archive (at most20MiB, retained3days). No full repository test/build or release step is included.

References:
- https://www.rfc-editor.org/rfc/rfc8216.html#section-4.1
- https://www.rfc-editor.org/rfc/rfc3986.html#section-5.4.1
- https://github.com/bytedance/xgplayer/blob/65b6a0ec223e3a92d55c4453df419197806967d4/packages/xgplayer-hls/src/hls/manifest-loader/parser/utils.js
