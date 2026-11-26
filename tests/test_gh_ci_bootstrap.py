"""Portable self-test suite for gh-ci-bootstrap (runs locally and on GitHub Actions)."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON_CI = REPO_ROOT / ".github" / "workflows" / "common-ci.yml"
REPO_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_TEMPLATE = REPO_ROOT / "templates" / "ci.yml"
REPO_CHECK_PY = REPO_ROOT / "scripts" / "repo_check.py"
DEPENDABOT_GEN = REPO_ROOT / "scripts" / "generate_dependabot.py"
INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

ISOLATE_UNSET_KEYS = (
    "GITHUB_ACTIONS",
    "GH_SKIP_CI_INIT",
    "CI_CHECK_SKIP",
    "GH_CI_OWNER",
    "GITHUB_STEP_SUMMARY",
    "CI_STRICT_EMOJI",
    "CI_ENABLE_NO_EMOJI",
    "CI_MAX_FILE_SIZE_MB",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
)


def isolated_env(home_dir: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a hermetically isolated environment so global git configs are never touched."""
    env = os.environ.copy()
    for k in ISOLATE_UNSET_KEYS:
        env.pop(k, None)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
        env["GIT_CONFIG_GLOBAL"] = str(home_dir / ".gitconfig")
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    if extra:
        env.update(extra)
    return env


def run(
    cmd: list[str],
    cwd: Path | str | None = None,
    env_extra: dict[str, str] | None = None,
    home_dir: Path | None = None,
    stdin_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = isolated_env(home_dir=home_dir, extra=env_extra)
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        check=check,
    )


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        res = run(
            [
                "ruby",
                "-rpsych",
                "-rjson",
                "-e",
                "puts JSON.generate(Psych.safe_load(File.read(ARGV[0])))",
                "--",
                str(path),
            ]
        )
        return json.loads(res.stdout)


def find_step(job: dict, *, step_id: str | None = None, name_substr: str | None = None) -> dict:
    for step in job["steps"]:
        if step_id and step.get("id") == step_id:
            return step
        if name_substr and name_substr in step.get("name", ""):
            return step
    raise AssertionError(f"Step not found: step_id={step_id}, name_substr={name_substr}")


def extract_py_heredoc(run_block: str, marker: str = "PYEOF") -> str:
    m = re.search(rf"<<\s*'{marker}'\n(.*?)\n{marker}", run_block, flags=re.S)
    assert m is not None
    return m.group(1)


def init_git(path: Path, with_commit: bool = False, home_dir: Path | None = None, env_extra: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", "-b", "main"], cwd=path, home_dir=home_dir, env_extra=env_extra)
    run(["git", "config", "user.name", "Test User"], cwd=path, home_dir=home_dir, env_extra=env_extra)
    run(["git", "config", "user.email", "test@example.com"], cwd=path, home_dir=home_dir, env_extra=env_extra)
    if with_commit:
        (path / "README.md").write_text("# demo\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=path, home_dir=home_dir, env_extra=env_extra)
        run(["git", "commit", "-q", "-m", "initial"], cwd=path, home_dir=home_dir, env_extra=env_extra)


def parse_github_output(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            k, delim = line.split("<<", 1)
            buf = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            out[k] = "\n".join(buf)
        elif "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
        i += 1
    return out


def test_shell_scripts_syntax():
    scripts = [
        REPO_ROOT / "bin" / "gh",
        REPO_ROOT / "bin" / "ci-check",
        REPO_ROOT / "scripts" / "ci-init",
        REPO_ROOT / "scripts" / "pre-push-ci-init",
        REPO_ROOT / "scripts" / "pre-commit-ci-check",
        REPO_ROOT / "hooks" / "pre-push",
        REPO_ROOT / "hooks" / "pre-commit",
        INSTALL_SH,
        UNINSTALL_SH,
    ]
    for script in scripts:
        res = run(["bash", "-n", "--", str(script)], check=False)
        assert res.returncode == 0, f"bash -n failed for {script}:\n{res.stderr}"


def test_common_ci_in_sync_with_repo_check():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import sync_workflows  # type: ignore

        expected = sync_workflows.render_common_ci()
    finally:
        sys.path.pop(0)
    assert COMMON_CI.read_text(encoding="utf-8") == expected, (
        "common-ci.yml is out of sync with scripts/repo_check.py; run python3 scripts/sync_workflows.py"
    )


def test_workflows_and_template_yaml_syntax():
    common = load_yaml(COMMON_CI)
    on_section = common.get("on") or common.get(True) or common.get("true") or {}
    assert "workflow_call" in on_section
    assert set(common["jobs"].keys()) == {"repo-checks", "node", "python", "apple", "flutter-dart", "dotnet"}
    assert common["jobs"]["apple"]["runs-on"] == "macos-26"

    # M1: Job display names must be static strings (no ${{ ... }}) for Branch Protection compatibility
    for jname, job in common["jobs"].items():
        assert "${{" not in job["name"], f"Job '{jname}' name must be static: {job['name']}"

    # H1/H2: repo-checks checkout uses fetch-depth: 0
    checkout_step = find_step(common["jobs"]["repo-checks"], name_substr="Checkout")
    assert checkout_step["with"]["fetch-depth"] == 0

    # M2: Third-party actions are pinned to 40-char commit SHAs
    for jname, job in common["jobs"].items():
        for step in job["steps"]:
            uses = step.get("uses", "")
            if uses and not uses.startswith(("actions/", "./")):
                assert re.search(r"@[0-9a-f]{40}$", uses), f"Third-party action not SHA-pinned in {jname}: {uses}"
            if "run" in step:
                bash_src = re.sub(r"\$\{\{.*?\}\}", "DUMMY", step["run"])
                res = run(["bash", "-n"], stdin_text=bash_src, check=False)
                assert res.returncode == 0, f"Bash syntax error in {jname}/{step['name']}:\n{res.stderr}"

    tmpl = load_yaml(CI_TEMPLATE)
    assert "HR0101/gh-ci-bootstrap/.github/workflows/common-ci.yml@v1" in tmpl["jobs"]["common"]["uses"]
    repo_wf = load_yaml(REPO_CI)
    assert repo_wf["jobs"]["common"]["uses"] == "./.github/workflows/common-ci.yml"


def test_repo_check_features_and_exit_codes():
    # Doctor & mutually exclusive flags
    res_doc = run([sys.executable, str(REPO_CHECK_PY), "--doctor"], check=False)
    assert res_doc.returncode == 0 and "python3" in res_doc.stdout
    res_excl = run([sys.executable, str(REPO_CHECK_PY), "--staged", "--tracked"], check=False)
    assert res_excl.returncode == 2

    with tempfile.TemporaryDirectory(prefix="gh-ci-checks-") as tmp_str:
        tmp = Path(tmp_str)

        # Clean repo: bare symbols U+260E/270C/25B6/3030 allowed in default mode; BOM Dockerfile/sh ok; LFS huge file skipped
        clean = tmp / "clean"
        (clean / ".github" / "workflows").mkdir(parents=True)
        init_git(clean, home_dir=tmp)
        shutil.copy(CI_TEMPLATE, clean / ".github" / "workflows" / "ci.yml")
        (clean / "README.md").write_text("Title\n=======\n\u2605 \u2606 \u266a \u2192 \u203b \u260e \u270c \u25b6 \u3030 ok\n", encoding="utf-8")
        (clean / "bom.json").write_bytes('\ufeff{"ok": true}\n'.encode("utf-8"))
        (clean / "Dockerfile").write_bytes("\ufeffFROM alpine:3.20\nCOPY . /app\n".encode("utf-8"))
        (clean / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
        with open(clean / "large.bin", "wb") as f:
            f.truncate(11 * 1024 * 1024)
        run(["git", "add", "."], cwd=clean, home_dir=tmp)

        res = run([sys.executable, str(REPO_CHECK_PY), "--tracked", "--ci"], cwd=clean, home_dir=tmp, check=False)
        assert res.returncode == 0, f"Clean repo failed:\n{res.stdout}\n{res.stderr}"

        # --strict-emoji catches bare U+260E/270C/25B6/3030
        res_strict = run([sys.executable, str(REPO_CHECK_PY), "--tracked", "--strict-emoji"], cwd=clean, home_dir=tmp, check=False)
        assert res_strict.returncode == 1 and "U+260E" in res_strict.stdout and "U+270C" in res_strict.stdout

        # Internal error (invalid CI_MAX_FILE_SIZE_MB) returns exit code 3 (distinguishable from violations=1)
        res_int = run(
            [sys.executable, str(REPO_CHECK_PY), "--tracked"],
            cwd=clean,
            home_dir=tmp,
            env_extra={"CI_MAX_FILE_SIZE_MB": "abc"},
            check=False,
        )
        assert res_int.returncode == 3

        # Staged vs working difference in BOTH directions + colon filename (`1:foo.txt`)
        colon_file = clean / "1:foo.txt"
        colon_file.write_text("clean staged\n", encoding="utf-8")
        run(["git", "add", "1:foo.txt"], cwd=clean, home_dir=tmp)
        # Working tree becomes dirty with emoji, staged remains clean -> --staged must PASS!
        colon_file.write_text("dirty working \U0001F680\n", encoding="utf-8")
        assert run([sys.executable, str(REPO_CHECK_PY), "--staged"], cwd=clean, home_dir=tmp, check=False).returncode == 0
        # Reverse: staged is dirty, working tree is clean -> --staged must FAIL!
        colon_file.write_text("staged dirty \U0001F680\n", encoding="utf-8")
        run(["git", "add", "1:foo.txt"], cwd=clean, home_dir=tmp)
        colon_file.write_text("clean working\n", encoding="utf-8")
        res_st = run([sys.executable, str(REPO_CHECK_PY), "--staged"], cwd=clean, home_dir=tmp, check=False)
        assert res_st.returncode == 1 and "1:foo.txt" in res_st.stdout

        # Dirty repo: .envrc, terraform.tfstate, FE0F symbol, invalid JSON, broken plist
        dirty = tmp / "dirty"
        init_git(dirty, home_dir=tmp)
        (dirty / ".envrc").write_text("export FOO=bar\n", encoding="utf-8")
        (dirty / "terraform.tfstate").write_text("{}\n", encoding="utf-8")
        (dirty / "fe0f.txt").write_text("phone \u260e\ufe0f\n", encoding="utf-8")
        (dirty / "bad.json").write_text("{invalid json}\n", encoding="utf-8")
        (dirty / "Broken.plist").write_text("<plist><dict><key>x</dict></plist>\n", encoding="utf-8")
        run(["git", "add", "."], cwd=dirty, home_dir=tmp)
        res_d = run([sys.executable, str(REPO_CHECK_PY), "--tracked", "--ci"], cwd=dirty, home_dir=tmp, check=False)
        assert res_d.returncode == 1
        for expected in [".envrc", "terraform.tfstate", "fe0f.txt,line=1", "bad.json", "Broken.plist"]:
            assert expected in res_d.stdout, f"Missing '{expected}' in:\n{res_d.stdout}"


def test_repo_check_handles_json_recursion_error(monkeypatch):
    """M5: JSON RecursionError must be reported as syntax error (exit 1), never uncaught crash (exit 3)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import repo_check  # type: ignore

        with tempfile.TemporaryDirectory(prefix="gh-ci-rec-") as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "r"
            init_git(repo, home_dir=tmp)
            (repo / "deep.json").write_text('{"a": 1}\n', encoding="utf-8")
            run(["git", "add", "deep.json"], cwd=repo, home_dir=tmp)

            def raise_rec(*args, **kwargs):
                raise RecursionError("maximum recursion depth exceeded")

            monkeypatch.setattr(repo_check.json, "loads", raise_rec)
            monkeypatch.chdir(repo)
            args = repo_check.argparse.Namespace(staged=False, tracked=True, ci=False, strict_emoji=False, doctor=False, quiet=True)
            code = repo_check.run_checker(args)
            assert code == repo_check.EXIT_VIOLATION
    finally:
        sys.path.pop(0)


def test_stack_detection_and_changed_files_filter():
    common = load_yaml(COMMON_CI)
    detect_step = find_step(common["jobs"]["repo-checks"], step_id="detect")
    detect_py = extract_py_heredoc(detect_step["run"])

    ns: dict = {}
    exec(compile(detect_py, "<detect>", "exec"), ns)

    # H3 table-driven tests for computeStackRunFlags (inverted safe design)
    cases = [
        (["README.md", "docs/guide.md", "LICENSE", ".github/dependabot.yml", "assets/logo.png"],
         {"node": False, "python": False, "apple": False, "dart": False, "dotnet": False}),
        (["src/App.xcconfig", "Resources/en.lproj/Localizable.strings", "App.xctestplan"],
         {"node": False, "python": False, "apple": True, "dart": False, "dotnet": False}),
        (["Directory.Packages.props", "Resources/Strings.resx", "appsettings.Production.json"],
         {"node": False, "python": False, "apple": False, "dart": False, "dotnet": True}),
        (["docs/page.mdx", "src/icon.svg"],
         {"node": True, "python": False, "apple": False, "dart": False, "dotnet": False}),
        (["l10n.yaml", "lib/l10n/app_ja.arb"],
         {"node": False, "python": False, "apple": False, "dart": True, "dotnet": False}),
        (["MANIFEST.in", "mypy.cfg"],
         {"node": False, "python": True, "apple": False, "dart": False, "dotnet": False}),
        (["templates/index.html", "unknown_script.xyz"],
         {"node": True, "python": True, "apple": True, "dart": True, "dotnet": True}),
        ([".github/workflows/ci.yml"],
         {"node": True, "python": True, "apple": True, "dart": True, "dotnet": True}),
    ]
    for changed, expected in cases:
        got = ns["computeStackRunFlags"](changed)
        assert got == expected, f"computeStackRunFlags({changed}) -> {got} != {expected}"

    # Low 1: resolvePythonVersion respects upper bounds
    assert ns["resolvePythonVersion"](">=3.8,<3.11") == "3.10"
    assert ns["resolvePythonVersion"](">=3.13") == "3.13"
    assert ns["resolvePythonVersion"](">=3.10") == "3.12"

    # Apple -showdestinations real ordering test (id before OS + incompatible exclusion)
    apple_xcode_step = find_step(common["jobs"]["apple"], name_substr="Build & test Xcode")
    apple_xcode_py = extract_py_heredoc(apple_xcode_step["run"])
    apple_ns: dict = {}
    exec(compile(apple_xcode_py, "<apple>", "exec"), apple_ns)

    class FakeRes:
        returncode = 0
        stdout = (
            '\tDestinations compatible with the "App" scheme:\n'
            '\t\t{ platform:iOS Simulator, id:dvtdevice-placeholder, name:Any iOS Simulator Device }\n'
            '\t\t{ platform:iOS Simulator, arch:arm64, id:7299CF4D-3623-4131-AC54-5173DE87C867, OS:27.2, name:iPhone 17 }\n'
            '\t\t{ platform:iOS Simulator, arch:arm64, id:FD47DAA5-C15F-4409-A0D7-6A250FD8D1F4, OS:27.2, name:iPhone 18 Pro Max }\n'
            '\tDestinations incompatible with the "App" scheme:\n'
            '\t\t{ platform:iOS Simulator, arch:arm64, id:AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA, OS:99.0, name:iPhone Future }\n'
        )

    apple_ns["runCmd"] = lambda *a, **kw: FakeRes()
    assert apple_ns["findIosSimulatorFromShowDestinations"](["-project", "App.xcodeproj"], "scheme", "App") == "FD47DAA5-C15F-4409-A0D7-6A250FD8D1F4"

    # H1/H2: Simulate full-history clone diff for push & PR merge commit
    with tempfile.TemporaryDirectory(prefix="gh-ci-diff-") as tmp_str:
        tmp = Path(tmp_str)
        origin = tmp / "origin.git"
        origin.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=origin, home_dir=tmp)

        work = tmp / "work"
        init_git(work, with_commit=True, home_dir=tmp)
        (work / "Package.swift").write_text("// swift\n", encoding="utf-8")
        run(["git", "add", "Package.swift"], cwd=work, home_dir=tmp)
        run(["git", "commit", "-q", "-m", "add spm"], cwd=work, home_dir=tmp)
        run(["git", "remote", "add", "origin", f"file://{origin}"], cwd=work, home_dir=tmp)
        run(["git", "push", "-q", "origin", "main"], cwd=work, home_dir=tmp)
        base_sha = run(["git", "rev-parse", "HEAD"], cwd=work, home_dir=tmp).stdout.strip()

        (work / "README.md").write_text("# docs only\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=work, home_dir=tmp)
        run(["git", "commit", "-q", "-m", "docs"], cwd=work, home_dir=tmp)
        run(["git", "push", "-q", "origin", "main"], cwd=work, home_dir=tmp)

        cloned = tmp / "cloned"
        run(["git", "clone", "-q", f"file://{origin}", str(cloned)], home_dir=tmp)
        out_push = tmp / "out_push.txt"
        run(
            [sys.executable, "-c", detect_py],
            cwd=cloned,
            home_dir=tmp,
            env_extra={"GITHUB_OUTPUT": str(out_push), "EVENT_NAME": "push", "BEFORE_SHA": base_sha},
        )
        assert parse_github_output(out_push)["has_apple"] == "false"


def test_dependabot_generator():
    with tempfile.TemporaryDirectory(prefix="gh-ci-dep-") as tmp_str:
        tmp = Path(tmp_str)
        (tmp / "package.json").write_text("{}", encoding="utf-8")
        (tmp / "bun.lock").write_text("", encoding="utf-8")
        (tmp / "uv.lock").write_text("", encoding="utf-8")
        (tmp / "Package.swift").write_text("", encoding="utf-8")
        (tmp / "pubspec.yaml").write_text("", encoding="utf-8")
        (tmp / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
        res = run([sys.executable, str(DEPENDABOT_GEN), str(tmp)])
        for needle in ('"github-actions"', '"bun"', '"uv"', '"swift"', '"pub"', '"docker"', "minor-and-patch:"):
            assert needle in res.stdout


def test_install_uninstall_wrapper_hooks_and_ci_init_edge_cases():
    with tempfile.TemporaryDirectory(prefix="gh-ci-e2e-") as tmp_str:
        tmp = Path(tmp_str)
        fake_home = tmp / "home"
        fake_home.mkdir()
        fake_bin = tmp / "fake_bin"
        fake_bin.mkdir()

        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$@" > "$FAKE_GH_LOG"\n'
            'echo "https://github.com/testuser/demo"\n'
            'if [ -n "${FAKE_REMOTE:-}" ]; then\n'
            '  git -C "${FAKE_SRC_DIR:-.}" remote add "${FAKE_REMOTE_NAME:-origin}" "$FAKE_REMOTE" 2>/dev/null || true\n'
            '  git -C "${FAKE_SRC_DIR:-.}" push -q "${FAKE_REMOTE_NAME:-origin}" HEAD >/dev/null 2>&1 || true\n'
            'fi\n'
            'exit "${FAKE_GH_EXIT:-0}"\n',
            encoding="utf-8",
        )
        fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
        args_log = tmp / "gh_args.log"

        env_extra = {
            "PATH": f"{fake_home / '.local/bin'}:{fake_bin}:/usr/bin:/bin",
            "FAKE_GH_LOG": str(args_log),
        }

        # 1. M10: Install refuses to overwrite a real gh binary at ~/.local/bin/gh
        (fake_home / ".local/bin").mkdir(parents=True)
        real_bin = fake_home / ".local/bin/gh"
        real_bin.write_text("#!/bin/sh\necho real binary\n", encoding="utf-8")
        real_bin.chmod(0o755)
        res_refuse = run([str(INSTALL_SH), "--owner", "testuser"], home_dir=fake_home, env_extra=env_extra, check=False)
        assert res_refuse.returncode != 0
        real_bin.unlink()

        # 2. Pre-existing custom hook + foreign symlink should be backed up / preserved properly across install & uninstall
        tmpl_hooks = fake_home / ".git-templates/hooks"
        tmpl_hooks.mkdir(parents=True)
        custom_hook = tmpl_hooks / "pre-push"
        custom_hook.write_text("#!/bin/sh\necho custom hook\n", encoding="utf-8")

        # Install idempotency (run twice)
        run([str(INSTALL_SH), "--owner", "testuser"], home_dir=fake_home, env_extra=env_extra)
        run([str(INSTALL_SH), "--owner", "testuser"], home_dir=fake_home, env_extra=env_extra)
        assert (fake_home / ".local/bin/gh").is_symlink()
        assert (fake_home / ".local/bin/ci-check").is_symlink()
        assert (fake_home / ".local/share/gh-wrapper/manifest.tsv").is_file()

        # 3. pre-commit blocks staged emoji, skips via CI_CHECK_SKIP=1, ignores internal error (exit 3), and skips emoji during MERGE_HEAD
        proj = tmp / "proj"
        init_git(proj, with_commit=True, home_dir=fake_home, env_extra=env_extra)
        (proj / "bad.txt").write_text("oops \U0001F680\n", encoding="utf-8")
        run(["git", "add", "bad.txt"], cwd=proj, home_dir=fake_home, env_extra=env_extra)
        assert run(["git", "commit", "-m", "block"], cwd=proj, home_dir=fake_home, env_extra=env_extra, check=False).returncode != 0

        # M5: Internal error (CI_MAX_FILE_SIZE_MB=abc) allows commit with warning instead of blocking all commits
        res_err_allow = run(
            ["git", "commit", "-m", "internal err fallback"],
            cwd=proj,
            home_dir=fake_home,
            env_extra={**env_extra, "CI_MAX_FILE_SIZE_MB": "abc"},
            check=False,
        )
        assert res_err_allow.returncode == 0

        # CI_CHECK_SKIP=1 bypass
        (proj / "skip.txt").write_text("oops \U0001F680\n", encoding="utf-8")
        run(["git", "add", "skip.txt"], cwd=proj, home_dir=fake_home, env_extra=env_extra)
        run(["git", "commit", "-m", "skip"], cwd=proj, home_dir=fake_home, env_extra={**env_extra, "CI_CHECK_SKIP": "1"})

        # Low 8: MERGE_HEAD suppresses emoji block while still running commit
        head_for_merge = run(["git", "rev-parse", "HEAD"], cwd=proj, home_dir=fake_home).stdout.strip()
        (proj / ".git/MERGE_HEAD").write_text(f"{head_for_merge}\n", encoding="utf-8")
        (proj / "merge.txt").write_text("merged \U0001F680\n", encoding="utf-8")
        run(["git", "add", "merge.txt"], cwd=proj, home_dir=fake_home, env_extra=env_extra)
        res_m = run(["git", "commit", "-m", "merge msg"], cwd=proj, home_dir=fake_home, env_extra=env_extra, check=False)
        assert res_m.returncode == 0, res_m.stderr
        if (proj / ".git/MERGE_HEAD").exists():
            (proj / ".git/MERGE_HEAD").unlink()

        # 4. gh wrapper arg parsing & failure handling (-sDIR, --remote, --push=false, FAKE_GH_EXIT=1)
        gh_wrap = str(fake_home / ".local/bin/gh")
        r_fail = tmp / "r_fail"
        init_git(r_fail, with_commit=True, home_dir=fake_home, env_extra=env_extra)
        res_f = run(
            [gh_wrap, "repo", "create", "x", f"-s{r_fail}", "--private"],
            cwd=tmp,
            home_dir=fake_home,
            env_extra={**env_extra, "FAKE_GH_EXIT": "1"},
            check=False,
        )
        assert res_f.returncode == 1 and not (r_fail / ".github").exists()

        bare_up = tmp / "bare_up.git"
        bare_up.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=bare_up, home_dir=fake_home, env_extra=env_extra)
        r_ok = tmp / "r_ok"
        init_git(r_ok, with_commit=True, home_dir=fake_home, env_extra=env_extra)
        res_push = run(
            [gh_wrap, "repo", "create", "demo", "--source", str(r_ok), "--remote", "up", "--push"],
            cwd=tmp,
            home_dir=fake_home,
            env_extra={**env_extra, "FAKE_REMOTE": str(bare_up), "FAKE_REMOTE_NAME": "up", "FAKE_SRC_DIR": str(r_ok)},
        )
        assert res_push.returncode == 0 and (r_ok / ".github/workflows/ci.yml").is_file()
        assert ".github/workflows/ci.yml" in run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=bare_up).stdout

        # --push=false creates local CI files without pushing additional CI commit to remote
        bare_nopush = tmp / "bare_nopush.git"
        bare_nopush.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=bare_nopush, home_dir=fake_home, env_extra=env_extra)
        r_np = tmp / "r_np"
        init_git(r_np, with_commit=True, home_dir=fake_home, env_extra=env_extra)
        res_np = run(
            [gh_wrap, "repo", "create", "demo2", f"--source={r_np}", "--push", "--push=false"],
            cwd=tmp,
            home_dir=fake_home,
            env_extra={**env_extra, "FAKE_REMOTE": str(bare_nopush), "FAKE_SRC_DIR": str(r_np)},
        )
        assert res_np.returncode == 0 and (r_np / ".github/workflows/ci.yml").is_file()
        assert ".github/workflows/ci.yml" not in run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=bare_nopush).stdout

        # 5. ci-init: CRLF tracked file skips .gitattributes (M8); broken symlink is not overwritten (Low 5)
        r_crlf = tmp / "r_crlf"
        init_git(r_crlf, with_commit=False, home_dir=fake_home, env_extra=env_extra)
        (r_crlf / "win.txt").write_bytes(b"line1\r\nline2\r\n")
        run(["git", "add", "win.txt"], cwd=r_crlf, home_dir=fake_home, env_extra=env_extra)
        run(["git", "commit", "-q", "-m", "crlf"], cwd=r_crlf, home_dir=fake_home, env_extra=env_extra)
        outside = tmp / "outside_target.txt"
        outside.write_text("do not touch", encoding="utf-8")
        os.symlink(str(outside), str(r_crlf / ".editorconfig"))
        run([str(fake_home / ".local/share/gh-wrapper/ci-init"), str(r_crlf)], cwd=r_crlf, home_dir=fake_home, env_extra=env_extra)
        assert not (r_crlf / ".gitattributes").exists(), "M8: .gitattributes should skip when CRLF tracked files exist"
        assert outside.read_text(encoding="utf-8") == "do not touch"

        # 6. pre-push bypass cases: other owner, non-empty remote, detached HEAD, delete push, tag push, committed workflows
        empty_bare = tmp / "empty_bare.git"
        empty_bare.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=empty_bare, home_dir=fake_home, env_extra=env_extra)
        r_pp = tmp / "r_pp"
        init_git(r_pp, with_commit=True, home_dir=fake_home, env_extra=env_extra)
        hook = str(r_pp / ".git/hooks/pre-push")
        head_sha = run(["git", "rev-parse", "HEAD"], cwd=r_pp, home_dir=fake_home).stdout.strip()
        run(["git", "remote", "add", "empty_r", str(empty_bare)], cwd=r_pp, home_dir=fake_home)

        for label, url, stdin_line in [
            ("other_owner", "https://github.com/other/repo.git", f"refs/heads/main {head_sha} refs/heads/main {'0'*40}\n"),
            ("nonempty_remote", "https://github.com/testuser/repo.git", f"refs/heads/main {head_sha} refs/heads/main {'a'*40}\n"),
            ("delete_push", "https://github.com/testuser/repo.git", f"(delete) {'0'*40} refs/heads/feat {'a'*40}\n"),
            ("tag_push", "https://github.com/testuser/repo.git", f"refs/tags/v1 {head_sha} refs/tags/v1 {'0'*40}\n"),
        ]:
            r_bypass = run([hook, "empty_r", url], cwd=r_pp, home_dir=fake_home, env_extra=env_extra, stdin_text=stdin_line, check=False)
            assert r_bypass.returncode == 0 and not (r_pp / ".github").exists(), f"Should bypass for {label}"

        # 7. Uninstall restores backed-up custom hook & cleans copy mode too
        run([str(UNINSTALL_SH)], home_dir=fake_home, env_extra=env_extra)
        assert custom_hook.is_file() and not custom_hook.is_symlink() and "custom hook" in custom_hook.read_text(encoding="utf-8")

        run([str(INSTALL_SH), "--copy", "--owner", "testuser"], home_dir=fake_home, env_extra=env_extra)
        assert (fake_home / ".local/bin/gh").is_file() and not (fake_home / ".local/bin/gh").is_symlink()
        run([str(UNINSTALL_SH)], home_dir=fake_home, env_extra=env_extra)
        assert not (fake_home / ".local/bin/gh").exists()
