"""Bounded native HLS parser comparison. No publication or package lifecycle scripts."""
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent

def check(value, message):
    if not value:
        raise RuntimeError(message)

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def tree_hash(entries):
    root = {}
    for entry in entries:
        parts = entry["path"].split("/")
        check(not entry["path"].startswith("/") and all(p not in ("", ".", "..") for p in parts), "Unsafe source path")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        check(parts[-1] not in node, "Duplicate source path")
        node[parts[-1]] = (entry["mode"], entry["sha"])
    def walk(node):
        rows = []
        for name, value in node.items():
            directory = isinstance(value, dict)
            mode, digest = ("40000", walk(value)) if directory else value
            rows.append((name.encode() + (b"/" if directory else b""),
                         mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(digest)))
        raw = b"".join(row[1] for row in sorted(rows))
        return hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    return walk(root)

def checked_tar(archive, expected):
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        files = []
        for member in members:
            parts = Path(member.name).parts
            check(parts and parts[0] == "package" and ".." not in parts and not member.name.startswith("/"), "Unsafe Yarn archive path")
            check(member.isfile() or member.isdir(), "Yarn archive links or special files are forbidden")
            if member.isfile():
                files.append(member)
        check(sorted(m.name.removeprefix("package/") for m in files) == sorted(expected["files"]), "Yarn archive file list mismatch")
        check(sum(m.size for m in files) == expected["expanded_bytes"], "Yarn expanded size mismatch")
        return members

def assess_jest(data, expected_names, failures):
    check(data.get("numRuntimeErrorTestSuites", 0) == 0, "Jest runtime/setup failure")
    check(data.get("numPendingTests", 0) == 0 and data.get("numTodoTests", 0) == 0, "Skipped/todo tests")
    cases = [case for suite in data["testResults"] for case in suite["assertionResults"]]
    check(len(cases) == len(expected_names) and {c["fullName"] for c in cases} == set(expected_names), "Native test identity mismatch")
    failed = {c["fullName"] for c in cases if c["status"] == "failed"}
    check(failed == set(failures), "Unexpected failing native tests")
    check(all(c["status"] in ("passed", "failed") for c in cases), "Unfinished native test")
    return [{"name": c["fullName"], "status": c["status"], "failureMessages": c.get("failureMessages", [])} for c in cases]

def assess_hls(data, source, expected_counts):
    check(data.get("numRuntimeErrorTestSuites", 0) == 0, "HLS runtime/setup failure")
    check(data.get("numPendingTests", 0) == 0 and data.get("numTodoTests", 0) == 0, "HLS skipped/todo tests")
    check(len(data["testResults"]) == len(expected_counts), "HLS suite count mismatch")
    result = []
    seen = set()
    for suite in data["testResults"]:
        path = str(Path(suite["name"]).resolve().relative_to(source.resolve()))
        check(path in expected_counts and path not in seen, "Unexpected or duplicate HLS suite")
        seen.add(path)
        cases = suite["assertionResults"]
        check(len(cases) == expected_counts[path], "HLS case count mismatch: " + path)
        check(len({c["fullName"] for c in cases}) == len(cases), "Duplicate HLS case identity")
        check(all(c["status"] in ("passed", "failed") for c in cases), "Unfinished HLS case")
        result.extend({"file": path, "name": c["fullName"], "status": c["status"], "failureMessages": c.get("failureMessages", [])} for c in cases)
    check(data["numTotalTests"] == sum(expected_counts.values()), "HLS total test count mismatch")
    return result

def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    source = Path(sys.argv[1]).resolve(strict=True)
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    work, out = temp / "xgplayer-query-work", temp / "xgplayer-query-evidence"
    work.mkdir(exist_ok=False)
    out.mkdir(exist_ok=False)
    for directory in ("tmp", "tooling", "yarn-cache"):
        (work / directory).mkdir()
    (work / "empty.npmrc").write_text("")
    env = {
        "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "CI": "true", "NODE_ENV": "test", "FORCE_COLOR": "0",
        "TMPDIR": str(work / "tmp"), "TMP": str(work / "tmp"), "TEMP": str(work / "tmp"),
        "npm_config_userconfig": str(work / "empty.npmrc"),
        "npm_config_cache": str(work / "npm-cache"),
        "npm_config_ignore_scripts": "true", "npm_config_update_notifier": "false",
        "YARN_IGNORE_SCRIPTS": "1", "YARN_ENABLE_TELEMETRY": "0",
    }
    started = time.monotonic()
    node = None
    runtime_root = None
    report = {
        "status": "not_validated", "source_commit": manifest["source_commit"],
        "source_tree": manifest["source_tree"], "steps": [], "source_checks": [],
        "native_results": {}, "broader_results": {}, "candidate_lint_exit": None, "scoped_fix_verified": False,
        "all_requested_checks_passed": False, "child_environment_keys": sorted(env),
        "limits": manifest["limits"], "artifact_complete": True,
        "run": {k: os.environ.get(k) for k in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")},
    }
    def save():
        (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    def usage():
        seen, total = set(), 0
        roots = [source, HERE.parent, work, out]
        if runtime_root:
            roots.append(runtime_root)
        for root in roots:
            for directory, dirs, files in os.walk(root, followlinks=False):
                for name in ["."] + dirs + files:
                    try:
                        s = (Path(directory) / name).lstat()
                        identity = (s.st_dev, s.st_ino)
                        if identity not in seen:
                            seen.add(identity)
                            total += s.st_blocks * 512
                    except FileNotFoundError:
                        pass
        return total
    def stop(proc):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
    def run(name, argv, seconds, cwd=source, install=False):
        remaining = manifest["limits"]["overall_seconds"] - (time.monotonic() - started)
        check(remaining > 0, "Overall execution budget exhausted")
        deadline = time.monotonic() + min(seconds, remaining)
        step = {"name": name, "argv": argv, "cwd": str(cwd), "status": "running", "peak_observed_bytes": 0}
        report["steps"].append(step)
        save()
        path = out / (name + ".log")
        count, recent = 0, ""
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        failure = None
        try:
            with path.open("wb") as log:
                while selector.get_map():
                    if time.monotonic() > deadline:
                        failure = "command/overall timeout"
                    used = usage()
                    step["peak_observed_bytes"] = max(step["peak_observed_bytes"], used)
                    if used > manifest["limits"]["workspace_bytes"]:
                        failure = "workspace exceeds 2 GiB"
                    if shutil.disk_usage(work).free < manifest["limits"]["minimum_free_bytes"]:
                        failure = "runner free disk below safety margin"
                    if failure:
                        stop(proc)
                    for key, _ in selector.select(timeout=1):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        count += len(chunk)
                        if count > manifest["limits"]["log_bytes"]:
                            failure = "command log exceeds bound"
                            stop(proc)
                            break
                        log.write(chunk)
                        recent = (recent + chunk.decode("utf-8", "replace"))[-8192:]
                        if install and any(s in recent for s in ("EAI_AGAIN", "ENOTFOUND", "ETIMEDOUT", "There appears to be trouble with your network connection", "Request failed")):
                            failure = "network error; no retry"
                            stop(proc)
                    if failure:
                        break
                if not failure:
                    proc.wait(timeout=5)
        finally:
            selector.close()
            if proc.poll() is None:
                stop(proc)
            step.update({"exit_code": proc.returncode, "status": "stopped" if failure else "completed", "stop_reason": failure, "log_bytes": min(count, manifest["limits"]["log_bytes"])})
            save()
        check(not failure, name + ": " + str(failure))
        return proc.returncode
    def git(*args, cwd=source):
        result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, timeout=20)
        check(result.returncode == 0 and len(result.stdout) < 2 * 1024 * 1024, "Read-only Git metadata command failed")
        return result.stdout
    def verify_source(stage, overrides=None):
        overrides = overrides or {}
        actual = []
        for entry in manifest["source_files"]:
            path = source / entry["path"]
            if entry["mode"] == "120000":
                check(path.is_symlink() and entry["path"] == "CLAUDE.md" and os.readlink(path) == "AGENTS.md", "Unexpected symlink")
                data = os.readlink(path).encode()
            else:
                check(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode), "Unexpected source file type")
                data = path.read_bytes()
            digest = blob(data)
            check(digest == overrides.get(entry["path"], entry["sha"]), "Unexpected source mutation: " + entry["path"])
            actual.append({**entry, "sha": digest, "size": len(data)})
        receipt = {"stage": stage, "count": len(actual), "tree": tree_hash(actual), "overrides": overrides, "files": actual}
        (out / ("source-" + stage + ".json")).write_text(json.dumps(receipt, indent=2) + "\n")
        report["source_checks"].append({k: receipt[k] for k in ("stage", "count", "tree", "overrides")})
        save()
    def jest(name, names, failures=()):
        result = out / (name + ".json")
        argv = [node, str(source / "node_modules/jest/bin/jest.js"), "--config", str(source / "jest.config.js"),
                "--runTestsByPath", str(source / manifest["test_path"]), "--runInBand", "--verbose=false", "--silent",
                "--json", "--outputFile", str(result)]
        code = run(name, argv, 180)
        check(code == (1 if failures else 0), "Unexpected Jest process result: " + name)
        data = json.loads(result.read_text())
        cases = assess_jest(data, names, failures)
        report["native_results"][name] = {"passed": data["numPassedTests"], "failed": data["numFailedTests"], "total": data["numTotalTests"], "cases": cases}
        save()
        return cases
    def evidence_archive():
        save()
        files = sorted(out.iterdir())
        check(all(p.is_file() and not p.is_symlink() for p in files), "Unexpected evidence path")
        check(sum(p.stat().st_size for p in files) <= manifest["limits"]["artifact_bytes"], "Evidence exceeds artifact bound")
        destination = temp / "xgplayer-query-evidence.tar.gz"
        with tarfile.open(destination, "w:gz") as tar:
            for path in files:
                tar.add(path, arcname=path.name, recursive=False)
        check(destination.stat().st_size <= manifest["limits"]["artifact_bytes"], "Archive exceeds artifact bound")
    def hls_jest(name, candidate=False):
        result = out / (name + ".json")
        counts = dict(manifest["hls_test_counts"])
        if candidate:
            counts[manifest["test_path"]] += len(manifest["new_test_names"])
        argv = [node, str(source / "node_modules/jest/bin/jest.js"), "--config", str(source / "jest.config.js"),
                "--runTestsByPath", *[str(source / path) for path in counts], "--runInBand", "--verbose=false", "--silent",
                "--json", "--outputFile", str(result)]
        result_code = run(name, argv, 180)
        check(result_code in (0, 1), "Unexpected HLS Jest process result")
        data = json.loads(result.read_text())
        cases = assess_hls(data, source, counts)
        check(result_code == (1 if data["numFailedTests"] else 0), "HLS process/JSON outcome mismatch")
        report["broader_results"][name] = {"passed": data["numPassedTests"], "failed": data["numFailedTests"],
                                          "total": data["numTotalTests"], "exit_code": result_code, "cases": cases}
        save()
        return cases
    code = 1
    try:
        check(os.environ.get("GITHUB_REPOSITORY") == manifest["execution_repository"], "Unexpected execution repository")
        check(os.environ.get("GITHUB_REF") == manifest["execution_ref"], "Unexpected execution ref")
        check(os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "Expected GitHub-hosted push")
        check(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "Only the first hosted attempt is allowed")
        check(os.environ.get("RUNNER_OS") == "Linux" and os.environ.get("RUNNER_ARCH") == "X64", "Unexpected runner")
        check(git("rev-parse", "HEAD").decode().strip() == manifest["source_commit"], "Source commit mismatch")
        check(git("rev-parse", "HEAD^{tree}").decode().strip() == manifest["source_tree"], "Source tree mismatch")
        check(tree_hash(manifest["source_files"]) == manifest["source_tree"], "Manifest tree mismatch")
        payload_root = HERE.parent
        check(git("rev-list", "--parents", "-n", "1", "HEAD", cwd=payload_root).decode().split() == [os.environ["GITHUB_SHA"], manifest["validation_parent"]], "Validation parent mismatch")
        check(set(git("ls-files", "-z", cwd=payload_root).decode().rstrip("\0").split("\0")) == set(manifest["payload_paths"]), "Unexpected validation payload files")
        for path, expected in manifest["candidate_files"].items():
            check(sha256((HERE / path).read_bytes()) == expected["sha256"], "Candidate payload mismatch")
        verify_source("initial")
        node = shutil.which("node")
        check(node is not None, "Node not present")
        check(Path(node).resolve().is_relative_to(Path(os.environ["RUNNER_TOOL_CACHE"]).resolve()), "Node must be the setup-node tool-cache runtime")
        runtime_root = Path(node).resolve().parent.parent
        check(run("node-version", [node, "--version"], 15) == 0, "Node version command failed")
        check((out / "node-version.log").read_text().strip() == manifest["node_version"], "Node version mismatch")
        yarn_info = manifest["yarn"]
        archive = work / "yarn.tgz"
        with urllib.request.urlopen(yarn_info["url"], timeout=30) as response:
            check(response.geturl() == yarn_info["url"], "Unexpected Yarn redirect")
            data = response.read(yarn_info["tarball_bytes"] + 1)
        check(len(data) == yarn_info["tarball_bytes"], "Yarn tarball byte count mismatch")
        check(hashlib.sha512(data).hexdigest() == yarn_info["sha512"], "Yarn tarball digest mismatch")
        archive.write_bytes(data)
        checked_tar(archive, yarn_info)
        with tarfile.open(archive) as tar:
            tar.extractall(work / "tooling", filter="data")
        yarn = work / "tooling/package/bin/yarn.js"
        check(run("yarn-version", [node, str(yarn), "--version"], 15) == 0, "Yarn version command failed")
        check((out / "yarn-version.log").read_text().strip() == "1.22.22", "Yarn version mismatch")
        install = [node, str(yarn), "install", "--frozen-lockfile", "--ignore-scripts", "--non-interactive", "--no-default-rc",
                   "--cache-folder", str(work / "yarn-cache"), "--registry", "https://registry.npmjs.org",
                   "--network-concurrency", "4", "--network-timeout", "30000"]
        check(run("install", install, manifest["limits"]["install_seconds"], install=True) == 0, "Frozen install failed; no retry")
        verify_source("after-install")
        versions = {}
        for package, version in manifest["tool_versions"].items():
            actual = json.loads((source / "node_modules" / package / "package.json").read_text())["version"]
            check(actual == version, "Native tool version mismatch: " + package)
            versions[package] = actual
        (out / "tool-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
        baseline_names = manifest["original_test_names"]
        new_names = manifest["new_test_names"]
        all_names = baseline_names + new_names
        jest("original-baseline", baseline_names)
        hls_base = hls_jest("hls-baseline")
        verify_source("after-hls-baseline")
        biome = [node, str(source / "node_modules/@biomejs/biome/bin/biome"), "check", manifest["production_path"], "--reporter=json"]
        report["baseline_lint_exit"] = run("baseline-lint", biome, 120)
        original_production = (source / manifest["production_path"]).read_bytes()
        candidate_production = (HERE / "utils.js").read_bytes()
        candidate_tests = (HERE / "parser.spec.js").read_bytes()
        (source / manifest["test_path"]).write_bytes(candidate_tests)
        test_overrides = {manifest["test_path"]: blob(candidate_tests)}
        both_overrides = {**test_overrides, manifest["production_path"]: blob(candidate_production)}
        verify_source("expanded", test_overrides)
        red = jest("expanded-red", all_names, new_names)
        (source / manifest["production_path"]).write_bytes(candidate_production)
        verify_source("candidate", both_overrides)
        jest("focused-green", all_names)
        report["candidate_lint_exit"] = run("candidate-lint", biome, 120)
        (source / manifest["production_path"]).write_bytes(original_production)
        verify_source("reverted", test_overrides)
        negative = jest("negative-control", all_names, new_names)
        check(red == negative, "Production-only negative control differs from original failures")
        (source / manifest["production_path"]).write_bytes(candidate_production)
        verify_source("restored", both_overrides)
        jest("restored-green", all_names)
        hls_candidate = hls_jest("hls-candidate", candidate=True)
        verify_source("after-hls-candidate", both_overrides)
        base_cases = {(c["file"], c["name"]): c["status"] for c in hls_base}
        candidate_cases = {(c["file"], c["name"]): c["status"] for c in hls_candidate}
        expected_new = {(manifest["test_path"], name) for name in new_names}
        check(set(candidate_cases) == set(base_cases) | expected_new, "HLS existing case identity changed")
        check(all(candidate_cases[key] == status for key, status in base_cases.items()), "HLS existing case outcome changed")
        check(all(candidate_cases[key] == "passed" for key in expected_new), "HLS candidate regression case failed")
        report["scoped_fix_verified"] = True
        report["all_requested_checks_passed"] = report["candidate_lint_exit"] == 0 and all(result["exit_code"] == 0 for result in report["broader_results"].values())
        report["status"] = "passed" if report["all_requested_checks_passed"] else "native_fix_verified_checks_require_review"
        code = 0 if report["all_requested_checks_passed"] else 1
    except Exception as error:
        report["status"] = "failed"
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["final_workspace_bytes"] = usage()
        try:
            evidence_archive()
        except Exception as error:
            report["artifact_complete"] = False
            report["artifact_error"] = type(error).__name__ + ": " + str(error)
            save()
            code = 1
        summary = {k: report.get(k) for k in ("status", "scoped_fix_verified", "all_requested_checks_passed", "error", "elapsed_seconds", "final_workspace_bytes", "artifact_complete", "baseline_lint_exit", "candidate_lint_exit")}
        summary["native_counts"] = {name: {k: result[k] for k in ("passed", "failed", "total")} for name, result in report["native_results"].items()}
        summary["broader_counts"] = {name: {k: result[k] for k in ("passed", "failed", "total")} for name, result in report["broader_results"].items()}
        print(json.dumps(summary))
    return code

if __name__ == "__main__":
    raise SystemExit(main())
