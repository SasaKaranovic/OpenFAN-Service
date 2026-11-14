import subprocess

from openfan_service import version as v


def fresh(monkeypatch, **env):
    v.get_version.cache_clear()
    for k in ("OPENFAN_BUILD_VERSION", "OPENFAN_BUILD_HASH"):
        monkeypatch.delenv(k, raising=False)
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    return v.get_version()


def test_build_version_from_image(monkeypatch):
    info = fresh(monkeypatch, OPENFAN_BUILD_VERSION="2026-11-13", OPENFAN_BUILD_HASH="a1b2c3d")
    assert info.full == "v2026-11-13 [a1b2c3d]" and info.source == "build"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def test_version_from_git_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    git(tmp_path, "init", "-q")
    assert fresh(monkeypatch).full == "vdev [unknown]"          # no commits yet
    (tmp_path / "a.txt").write_text("1")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "x")
    info = fresh(monkeypatch)
    assert info.source == "git" and len(info.version) == 10 and not info.git_hash.endswith("-dirty")
    (tmp_path / "a.txt").write_text("2")                         # uncommitted change
    assert fresh(monkeypatch).git_hash.endswith("-dirty")
    (tmp_path / "new.txt").write_text("untracked files do not count")
    (tmp_path / "a.txt").write_text("1")
    assert not fresh(monkeypatch).git_hash.endswith("-dirty")


def test_no_git(monkeypatch, tmp_path):
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    assert fresh(monkeypatch).full == "vdev [unknown]"
