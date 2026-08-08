# -*- coding: utf-8 -*-

import io
import os
import tarfile
import zipfile

import pytest

from ruyipage._runtime import installer
from ruyipage._runtime.archive import extract_archive
from ruyipage._runtime.cli import main as runtime_main
from ruyipage._runtime.errors import RuntimeVerificationError, UnsafeArchiveError
from ruyipage._runtime.manifest import (
    FIREFOX_VERSION,
    RELEASE_TAG,
    RUNTIMES,
    runtime_url,
)
from ruyipage._runtime.resolver import resolve_firefox_path
from ruyipage._runtime.verify import sha256_file, verify_sha256


def _make_zip(path, entries):
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _make_tar_xz(path, entries, symlink=None):
    with tarfile.open(str(path), "w:xz") as tf:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink[0])
            info.type = tarfile.SYMTYPE
            info.linkname = symlink[1]
            tf.addfile(info)


@pytest.mark.parametrize(
    ("platform_key", "asset", "install_subdir"),
    [
        (
            "win64",
            "firefox-155.0a1.en-US.win64-20260803.zip",
            "firefox-155.0a1-v1.2.58-win64",
        ),
        (
            "linux-x86_64",
            "firefox-155.0a1.en-US.linux-x86_64.tar.xz",
            "firefox-155.0a1-v1.2.58-linux-x86_64",
        ),
    ],
)
def test_runtime_manifest_targets_latest_ruyipage_release(
    platform_key,
    asset,
    install_subdir,
):
    info = RUNTIMES[platform_key]

    assert RELEASE_TAG == "v1.2.58"
    assert FIREFOX_VERSION == "155.0a1"
    assert info["release"] == RELEASE_TAG
    assert info["version"] == FIREFOX_VERSION
    assert info["asset"] == asset
    assert info["install_subdir"] == install_subdir
    assert runtime_url(info) == (
        "https://github.com/LoseNine/ruyipage/releases/download/"
        "v1.2.58/{}".format(asset)
    )


def test_verify_sha256_rejects_mismatch(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"abc")

    assert sha256_file(str(archive))
    with pytest.raises(RuntimeVerificationError):
        verify_sha256(str(archive), "0" * 64)


def test_extract_zip_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    _make_zip(archive, {"../evil.txt": "bad", "firefox/firefox.exe": "ok"})

    with pytest.raises(UnsafeArchiveError):
        extract_archive(str(archive), str(tmp_path / "out"), "zip")
    assert not (tmp_path / "evil.txt").exists()


def test_extract_tar_rejects_symlink(tmp_path):
    archive = tmp_path / "bad.tar.xz"
    _make_tar_xz(archive, {"firefox/firefox": "ok"}, symlink=("firefox/out", "../../evil"))

    with pytest.raises(UnsafeArchiveError):
        extract_archive(str(archive), str(tmp_path / "out"), "tar.xz")


def test_install_from_file_fake_zip(monkeypatch, tmp_path):
    archive = tmp_path / "runtime.zip"
    _make_zip(archive, {"firefox/firefox.exe": "fake exe"})

    fake_info = {
        "name": "firefox",
        "version": "test",
        "release": "vtest",
        "platform": "win64",
        "asset": archive.name,
        "archive_type": "zip",
        "sha256": sha256_file(str(archive)),
        "executable": "firefox/firefox.exe",
        "install_subdir": "firefox-test-win64",
        "max_files": 100,
        "max_total_size": 1024 * 1024,
    }
    monkeypatch.setattr(installer, "runtime_info", lambda platform_key=None: dict(fake_info))

    result = installer.install(root=str(tmp_path / "cache"), from_file=str(archive))

    assert result["installed"] is True
    assert os.path.isfile(result["executable_path"])
    assert installer.is_installed(root=str(tmp_path / "cache")) is True


def test_install_from_file_does_not_require_matching_sha256(monkeypatch, tmp_path):
    archive = tmp_path / "runtime.zip"
    _make_zip(archive, {"firefox/firefox.exe": "fake exe"})

    fake_info = {
        "name": "firefox",
        "version": "test",
        "release": "vtest",
        "platform": "win64",
        "asset": archive.name,
        "archive_type": "zip",
        "sha256": "0" * 64,
        "executable": "firefox/firefox.exe",
        "install_subdir": "firefox-test-win64",
        "max_files": 100,
        "max_total_size": 1024 * 1024,
    }
    monkeypatch.setattr(installer, "runtime_info", lambda platform_key=None: dict(fake_info))

    result = installer.install(root=str(tmp_path / "cache"), from_file=str(archive))

    assert result["installed"] is True
    assert os.path.isfile(result["executable_path"])


def test_resolver_prefers_explicit_path(monkeypatch, tmp_path):
    explicit = tmp_path / "firefox.exe"
    explicit.write_text("fake")
    monkeypatch.setattr("ruyipage._runtime.resolver.get_executable_path", lambda strict=False: "runtime")

    assert resolve_firefox_path(str(explicit)) == str(explicit)


def test_cli_dry_run_outputs_plan(capsys, tmp_path):
    code = runtime_main(["install", "--dry-run", "--install-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 0
    assert "install plan" in captured.out
    assert "No files were downloaded" in captured.out
