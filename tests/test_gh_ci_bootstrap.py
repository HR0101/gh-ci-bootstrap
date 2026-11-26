"""Portable self-test suite for gh-ci-bootstrap (runs locally and on GitHub Actions)."""
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


def run(cmd, cwd=None, env_extra=None, stdin_text=None, check=True):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
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


def extract_py_heredoc(run_block: str, marker: str = "PYEOF") -> str:
    m = re.search(rf"<<\s*'{marker}'\n(.*?)\n{marker}", run_block, flags=re.S)
    assert m is not None
    return m.group(1)


def init_git(path: Path, with_commit: bool = False, env_extra: dict | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", "-b", "main"], cwd=path, env_extra=env_extra)
    run(["git", "config", "user.name", "Test User"], cwd=path, env_extra=env_extra)
    run(["git", "config", "user.email", "test@example.com"], cwd=path, env_extra=env_extra)
    if with_commit:
        (path / "README.md").write_text("# demo\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=path, env_extra=env_extra)
        run(["git", "commit", "-q", "-m", "initial"], cwd=path, env_extra=env_extra)


def parse_github_output(path: Path) -> dict:
    out = {}
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

    for jname, job in common["jobs"].items():
        for step in job["steps"]:
            if "run" in step:
                bash_src = re.sub(r"\$\{\{.*?\}\}", "DUMMY", step["run"])
                res = run(["bash", "-n"], stdin_text=bash_src, check=False)
                assert res.returncode == 0, f"Bash syntax error in {jname}/{step['name']}:\n{res.stderr}"

    tmpl = load_yaml(CI_TEMPLATE)
    assert "HR0101/gh-ci-bootstrap/.github/workflows/common-ci.yml@v1" in tmpl["jobs"]["common"]["uses"]

    repo_wf = load_yaml(REPO_CI)
    assert repo_wf["jobs"]["common"]["uses"] == "./.github/workflows/common-ci.yml"


def test_repo_check_clean_dirty_and_extended_emojis():
    with tempfile.TemporaryDirectory(prefix="gh-ci-checks-") as tmp_str:
        tmp = Path(tmp_str)

        # Clean repo (including Japanese symbols ★☆♪→※, custom YAML tags, Firebase public config, Apple plist, Dockerfile)
        clean = tmp / "clean"
        (clean / ".github" / "workflows").mkdir(parents=True)
        init_git(clean)
        shutil.copy(CI_TEMPLATE, clean / ".github" / "workflows" / "ci.yml")
        (clean / "README.md").write_text("Title\n=======\n\u2605 \u2606 \u266a \u2192 \u203b ok\n", encoding="utf-8")
        (clean / "cfn.yml").write_text("Resources:\n  B:\n    Name: !Sub '${AWS::StackName}-b'\n", encoding="utf-8")
        gkey = "AI" + "za" + "AbCdEfGhIjKlMnOpQrStUvWxYz098765432"
        (clean / "GoogleService-Info.plist").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>API_KEY</key><string>{gkey}</string></dict></plist>\n',
            encoding="utf-8",
        )
        (clean / "Dockerfile").write_text("FROM alpine:3.20\nCOPY . /app\n", encoding="utf-8")
        run(["git", "add", "."], cwd=clean)
        res = run([sys.executable, str(REPO_CHECK_PY), "--tracked", "--ci"], cwd=clean, check=False)
        assert res.returncode == 0, f"Clean repo failed:\n{res.stdout}\n{res.stderr}"

        # Dirty repo (standard emoji + newly covered bare symbols U+270C/2699/2702/23F8, secret with trailing '-', conflict, broken plist, broken Dockerfile)
        dirty = tmp / "dirty"
        init_git(dirty)
        (dirty / "emoji.txt").write_text("hi \U0001F680 \u270c \u2699 \u2702 \u23f8\n", encoding="utf-8")
        (dirty / "leak.py").write_text('K = "' + "AI" + "za" + 'AbCdEfGhIjKlMnOpQrStUvWxYz09876543-"\n', encoding="utf-8")
        (dirty / "conflict.txt").write_text("<" * 7 + " HEAD\nleft\n", encoding="utf-8")
        (dirty / "Broken.plist").write_text("<plist><dict><key>x</dict></plist>\n", encoding="utf-8")
        (dirty / "Dockerfile").write_text("COYP . /app\n", encoding="utf-8")
        run(["git", "add", "."], cwd=dirty)
        res = run([sys.executable, str(REPO_CHECK_PY), "--tracked", "--ci"], cwd=dirty, check=False)
        assert res.returncode == 1
        for expected in ["col=4", "col=6", "col=8", "col=10", "col=12", "leak.py,line=1", "conflict.txt,line=1", "Broken.plist", "COYP"]:
            assert expected in res.stdout, f"Missing '{expected}' in:\n{res.stdout}"


def test_stack_detection_and_changed_files_filter():
    common = load_yaml(COMMON_CI)
    detect_py = extract_py_heredoc(common["jobs"]["repo-checks"]["steps"][1]["run"])

    # Unit-test computeStackRunFlags directly for cost-saving path filter
    ns = {}
    exec(compile(detect_py, "<detect>", "exec"), ns)
    assert ns["computeStackRunFlags"](["README.md", ".gitignore", ".github/dependabot.yml"]) == {
        "node": False, "python": False, "apple": False, "dart": False, "dotnet": False
    }
    assert ns["computeStackRunFlags"](["src/index.ts"]) == {
        "node": True, "python": False, "apple": False, "dart": False, "dotnet": False
    }
    assert ns["computeStackRunFlags"](["Sources/App/ContentView.swift"]) == {
        "node": False, "python": False, "apple": True, "dart": False, "dotnet": False
    }
    assert ns["computeStackRunFlags"]([".github/workflows/ci.yml"]) == {
        "node": True, "python": True, "apple": True, "dart": True, "dotnet": True
    }

    with tempfile.TemporaryDirectory(prefix="gh-ci-detect-") as tmp_str:
        tmp = Path(tmp_str)
        r = tmp / "multi"
        (r / "My App.xcodeproj" / "project.xcworkspace").mkdir(parents=True)
        (r / "test").mkdir(parents=True)
        init_git(r)
        (r / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
        (r / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")
        (r / "pyproject.toml").write_text('[project]\nname="app"\nrequires-python=">=3.13"\n', encoding="utf-8")
        (r / "pubspec.yaml").write_text("name: a\ndependencies:\n  flutter:\n    sdk: flutter\n", encoding="utf-8")
        (r / "test" / "app_test.dart").write_text("void main() {}\n", encoding="utf-8")
        run(["git", "add", "."], cwd=r)

        out_file = tmp / "gh_out.txt"
        run(
            [sys.executable, "-c", detect_py],
            cwd=r,
            env_extra={"GITHUB_OUTPUT": str(out_file), "EVENT_NAME": "workflow_dispatch"},
        )
        kv = parse_github_output(out_file)
        assert kv["has_node"] == "true" and kv["node_pm"] == "yarn" and kv["node_yarn_berry"] == "true"
        assert kv["has_python"] == "true" and kv["python_version"] == "3.13"
        assert kv["has_apple"] == "true" and kv["apple_kind"] == "xcodeproj" and kv["apple_project"] == "My App.xcodeproj"
        assert kv["has_dart_flutter"] == "true" and kv["dart_kind"] == "flutter" and kv["has_dart_tests"] == "true"

        # Real git push diff: docs-only commit skips heavy language jobs
        run(["git", "commit", "-q", "-m", "initial"], cwd=r)
        before_sha = run(["git", "rev-parse", "HEAD"], cwd=r).stdout.strip()
        (r / "README.md").write_text("# updated docs\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=r)
        run(["git", "commit", "-q", "-m", "docs"], cwd=r)
        out_docs = tmp / "gh_out_docs.txt"
        run(
            [sys.executable, "-c", detect_py],
            cwd=r,
            env_extra={"GITHUB_OUTPUT": str(out_docs), "EVENT_NAME": "push", "BEFORE_SHA": before_sha},
        )
        kv_docs = parse_github_output(out_docs)
        assert kv_docs["has_node"] == "false"
        assert kv_docs["has_python"] == "false"
        assert kv_docs["has_apple"] == "false"
        assert kv_docs["has_dart_flutter"] == "false"


def test_dependabot_generator():
    with tempfile.TemporaryDirectory(prefix="gh-ci-dep-") as tmp_str:
        tmp = Path(tmp_str)
        (tmp / "package.json").write_text("{}", encoding="utf-8")
        (tmp / "uv.lock").write_text("", encoding="utf-8")
        (tmp / "Package.swift").write_text("", encoding="utf-8")
        (tmp / "pubspec.yaml").write_text("", encoding="utf-8")
        (tmp / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
        res = run([sys.executable, str(DEPENDABOT_GEN), str(tmp)])
        for eco in ('"github-actions"', '"npm"', '"uv"', '"swift"', '"pub"', '"docker"'):
            assert eco in res.stdout


def test_install_ci_check_precommit_wrapper_prepush_and_uninstall():
    with tempfile.TemporaryDirectory(prefix="gh-ci-e2e-") as tmp_str:
        tmp = Path(tmp_str)
        fake_home = tmp / "home"
        fake_home.mkdir()
        fake_bin = tmp / "fake_bin"
        fake_bin.mkdir()

        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            '#!/usr/bin/env bash\n'
            'echo "https://github.com/testuser/demo"\n'
            'if [ -n "${FAKE_REMOTE:-}" ]; then\n'
            '  git remote add origin "$FAKE_REMOTE" 2>/dev/null || true\n'
            '  git push -q origin HEAD >/dev/null 2>&1 || true\n'
            'fi\n'
            'exit "${FAKE_GH_EXIT:-0}"\n',
            encoding="utf-8",
        )
        fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)

        base_env = {
            "HOME": str(fake_home),
            "PATH": f"{fake_home / '.local/bin'}:{fake_bin}:/usr/bin:/bin",
        }

        # Install into isolated HOME
        run([str(INSTALL_SH), "--owner", "testuser"], env_extra=base_env)
        assert (fake_home / ".local/bin/gh").is_symlink()
        assert (fake_home / ".local/bin/ci-check").is_symlink()
        assert (fake_home / ".git-templates/hooks/pre-push").is_symlink()
        assert (fake_home / ".git-templates/hooks/pre-commit").is_symlink()

        # Test pre-commit blocks staged emoji, passes once cleaned, and allows untracked dirty working file
        proj = tmp / "proj"
        init_git(proj, with_commit=True, env_extra=base_env)
        assert (proj / ".git/hooks/pre-commit").is_file()

        (proj / "bad.txt").write_text("oops \U0001F680\n", encoding="utf-8")
        run(["git", "add", "bad.txt"], cwd=proj, env_extra=base_env)
        res_block = run(["git", "commit", "-m", "should block"], cwd=proj, env_extra=base_env, check=False)
        assert res_block.returncode != 0, "pre-commit should block staged emoji!"

        # Bypass with CI_CHECK_SKIP=1 works, or fix file -> ci-check & commit pass
        (proj / "bad.txt").write_text("clean text\n", encoding="utf-8")
        run(["git", "add", "bad.txt"], cwd=proj, env_extra=base_env)
        run(["ci-check", "--staged"], cwd=proj, env_extra=base_env)
        run(["git", "commit", "-m", "clean commit"], cwd=proj, env_extra=base_env)

        # Test gh wrapper with --source=. --push generates ci.yml + dependabot.yml + .editorconfig + .gitattributes
        bare = tmp / "remote.git"
        bare.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=bare, env_extra=base_env)
        (proj / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
        run(["git", "add", "package.json"], cwd=proj, env_extra=base_env)
        run(["git", "commit", "-q", "-m", "add pkg"], cwd=proj, env_extra=base_env)

        (proj / "staged.txt").write_text("keep staged\n", encoding="utf-8")
        run(["git", "add", "staged.txt"], cwd=proj, env_extra=base_env)

        res = run(
            [str(fake_home / ".local/bin/gh"), "repo", "create", "demo", "--source=.", "--private", "--push"],
            cwd=proj,
            env_extra={**base_env, "FAKE_REMOTE": str(bare)},
        )
        assert res.stdout.strip() == "https://github.com/testuser/demo"
        assert (proj / ".github/workflows/ci.yml").is_file()
        assert (proj / ".github/dependabot.yml").is_file()
        assert '"npm"' in (proj / ".github/dependabot.yml").read_text(encoding="utf-8")
        assert (proj / ".editorconfig").is_file()
        assert (proj / ".gitattributes").is_file()
        assert run(["git", "diff", "--cached", "--name-only"], cwd=proj, env_extra=base_env).stdout.strip() == "staged.txt"
        remote_files = run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=bare).stdout
        assert ".github/workflows/ci.yml" in remote_files
        assert ".github/dependabot.yml" in remote_files

        # Test pre-push hook on empty remote
        empty_bare = tmp / "empty.git"
        empty_bare.mkdir()
        run(["git", "init", "--bare", "-q", "-b", "main"], cwd=empty_bare, env_extra=base_env)

        proj2 = tmp / "proj2"
        init_git(proj2, with_commit=True, env_extra=base_env)
        run(["git", "remote", "add", "empty_remote", str(empty_bare)], cwd=proj2, env_extra=base_env)
        head_sha = run(["git", "rev-parse", "HEAD"], cwd=proj2, env_extra=base_env).stdout.strip()
        hook = proj2 / ".git/hooks/pre-push"
        res_pp = run(
            [str(hook), "empty_remote", "https://github.com/testuser/new-app.git"],
            cwd=proj2,
            env_extra=base_env,
            stdin_text=f"refs/heads/main {head_sha} refs/heads/main {'0'*40}\n",
            check=False,
        )
        assert res_pp.returncode == 1
        assert (proj2 / ".github/workflows/ci.yml").is_file()
        assert (proj2 / ".editorconfig").is_file()

        # Uninstall cleans up all owned symlinks
        run([str(UNINSTALL_SH)], env_extra=base_env)
        assert not (fake_home / ".local/bin/gh").exists()
        assert not (fake_home / ".local/bin/ci-check").exists()
        assert not (fake_home / ".git-templates/hooks/pre-push").exists()
        assert not (fake_home / ".git-templates/hooks/pre-commit").exists()
