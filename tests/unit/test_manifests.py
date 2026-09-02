"""A package manifest is a promise that an exact file is at an exact URL.

The two fields nobody should ever type by hand are the version and the hash: get
either wrong and `winget install` fails on a checksum mismatch for everyone, and
the fix is another pull request to a Microsoft-owned repo. So they are generated
from the artifact, and these tests cover the generation.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from scripts.make_manifests import PLACEHOLDER_HASH, PLACEHOLDER_VERSION, main

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _render(tmp_path, version="1.2.3", body=b"pretend this is an exe"):
    exe = tmp_path / "anime.exe"
    exe.write_bytes(body)
    out = tmp_path / "manifests"
    code = main([version, "--from-file", str(exe), "--out", str(out)])
    assert code == 0
    return out, hashlib.sha256(body).hexdigest()


def test_the_hash_comes_from_the_file_not_from_a_human(tmp_path):
    out, expected = _render(tmp_path)
    installer = (out / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    assert expected in installer
    assert PLACEHOLDER_HASH not in installer


def test_every_manifest_carries_the_same_version(tmp_path):
    """winget rejects a manifest set whose three files disagree, and the error
    it gives points at the file, not at the disagreement."""
    out, _ = _render(tmp_path, version="9.9.9")
    for path in (out / "winget").glob("*.yaml"):
        text = path.read_text()
        assert "9.9.9" in text, path.name
        assert PLACEHOLDER_VERSION not in text, f"{path.name} kept the placeholder"


def test_the_download_url_points_at_the_tag_being_released(tmp_path):
    """The release asset is named for its version, so the URL and the filename
    have to move together — a manifest pointing at last release's file installs
    last release."""
    out, _ = _render(tmp_path, version="4.5.6")
    installer = (out / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    assert "releases/download/v4.5.6/anime-sh-4.5.6-windows-x64.exe" in installer


def test_the_scoop_manifest_still_parses_after_substitution(tmp_path):
    """It is JSON, and a template substitution is perfectly capable of producing
    something that no longer is."""
    out, expected = _render(tmp_path, version="2.0.0")
    data = json.loads((out / "scoop" / "anime-sh.json").read_text())
    assert data["version"] == "2.0.0"
    assert data["architecture"]["64bit"]["hash"] == expected
    assert "v2.0.0" in data["architecture"]["64bit"]["url"]


def test_a_version_that_is_not_a_version_is_refused(tmp_path):
    exe = tmp_path / "anime.exe"
    exe.write_bytes(b"x")
    assert main(["v1.2.3", "--from-file", str(exe), "--out", str(tmp_path / "o")]) == 2
    assert main(["latest", "--from-file", str(exe), "--out", str(tmp_path / "o")]) == 2


def test_a_hash_that_is_not_a_hash_is_refused(tmp_path):
    assert main(["1.2.3", "--sha256", "nope", "--out", str(tmp_path / "o")]) == 2


def test_a_missing_artifact_is_refused_rather_than_hashed_as_nothing(tmp_path):
    assert main(["1.2.3", "--from-file", str(tmp_path / "gone.exe"),
                 "--out", str(tmp_path / "o")]) == 2


@pytest.mark.parametrize("name", [
    "AnimeshSharma.anime-sh.yaml",
    "AnimeshSharma.anime-sh.locale.en-US.yaml",
    "AnimeshSharma.anime-sh.installer.yaml",
])
def test_the_committed_templates_are_valid_yaml(name):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((ROOT / "packaging" / "winget" / name).read_text())
    assert data["PackageIdentifier"] == "AnimeshSharma.anime-sh"


def test_the_portable_installer_declares_its_commands():
    """`InstallerType: portable` is what puts a single .exe on PATH without
    running an installer. Without `Commands`, winget installs the file and
    leaves no way to run it."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(
        (ROOT / "packaging" / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    )
    assert data["InstallerType"] == "portable"
    # Exactly one, not both. `winget validate` refuses a portable installer that
    # declares more than one command, and it refuses it at submission time —
    # after a maintainer has already been asked to look at the pull request.
    assert data["Commands"] == ["anime"]
    # NestedInstallerFiles describes a file inside an archive; this installer is
    # the bare executable, and including it fails winget's validation.
    assert "NestedInstallerFiles" not in data["Installers"][0]


def test_the_release_workflow_builds_and_verifies_the_bundle():
    """The bundle is what winget and scoop install. If the release stops
    producing it, the manifests point at a URL that 404s and every new install
    fails — while the release itself looks green."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())

    assert "bundle-windows" in wf["jobs"], "nothing builds the standalone exe"
    assert "bundle-windows" in wf["jobs"]["github-release"]["needs"], (
        "the release can publish without waiting for the bundle"
    )
    steps = wf["jobs"]["bundle-windows"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert any("must actually work" in n for n in names), (
        "the bundle is built but never run before being published"
    )


def test_the_release_attaches_the_versioned_executable():
    """winget pins an exact filename. Publishing it under a name the manifests
    do not expect is the same as not publishing it."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "anime-sh-${GITHUB_REF_NAME#v}-windows-x64.exe" in text
    assert '"$asset.sha256"' in text, "no checksum published beside the binary"


def test_the_checksum_is_taken_after_the_binary_gets_its_release_name():
    """`sha256sum` writes the filename into the file, and scoop reads that name
    back out. Hashing `anime.exe` and renaming the binary afterwards produced a
    checksum file that named a file the release does not contain: scoop searched
    it for `anime-sh-<version>-windows-x64.exe`, found nothing, and fell back to
    downloading 21 MB to hash itself. The published checksum verified nothing.
    """
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    steps = wf["jobs"]["bundle-windows"]["steps"]
    run = next(s["run"] for s in steps if "Checksum" in s.get("name", ""))
    mv = run.index("mv anime.exe")
    assert "sha256sum" in run[mv:], "the checksum is taken before the rename"
    assert "sha256sum anime.exe" not in run, "still hashing the pre-rename name"


def test_scoop_looks_for_the_checksum_where_the_release_puts_it():
    """The two halves of this only ever break together, silently: scoop derives
    the name it searches for from the installer URL, so if the checksum URL
    stops matching that shape, autoupdate degrades to a full download and the
    hash it records is one nothing cross-checked."""
    data = json.loads((ROOT / "packaging" / "scoop" / "anime-sh.json").read_text())
    installer = data["autoupdate"]["architecture"]["64bit"]["url"]
    # Everything after `#/` renames the download; the hash file is named for the
    # part before it, which is what actually lives on the release page.
    published = installer.split("#/")[0].rsplit("/", 1)[-1]
    hash_url = data["autoupdate"]["hash"]["url"]
    assert hash_url.rsplit("/", 1)[-1] == f"{published}.sha256", (
        f"scoop would fetch {hash_url!r} for an asset named {published!r}"
    )


def test_the_scoop_manifest_can_follow_new_releases_on_its_own():
    """A bucket nobody updates is worse than no bucket: it keeps installing an
    old version forever, silently."""
    data = json.loads((ROOT / "packaging" / "scoop" / "anime-sh.json").read_text())
    assert "checkver" in data and "autoupdate" in data
    assert "$version" in data["autoupdate"]["architecture"]["64bit"]["url"]


def test_the_release_date_is_filled_in(tmp_path):
    """winget records when a version was published. The template holds the epoch
    so an ungenerated manifest is obviously wrong rather than subtly wrong — but
    shipping that epoch would be worse than either."""
    yaml = pytest.importorskip("yaml")
    exe = tmp_path / "anime.exe"
    exe.write_bytes(b"x")
    out = tmp_path / "m"
    assert main(["1.2.3", "--from-file", str(exe), "--out", str(out),
                 "--release-date", "2026-08-30"]) == 0
    data = yaml.safe_load(
        (out / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    )
    assert str(data["ReleaseDate"]) == "2026-08-30"


def test_a_release_date_that_is_not_a_date_is_refused(tmp_path):
    exe = tmp_path / "anime.exe"
    exe.write_bytes(b"x")
    assert main(["1.2.3", "--from-file", str(exe), "--out", str(tmp_path / "o"),
                 "--release-date", "yesterday"]) == 2


def test_submitted_winget_manifests_carry_no_house_comments(tmp_path):
    """The templates explain themselves for whoever regenerates them here. A
    pull request to microsoft/winget-pkgs is not the place for our reasoning —
    the file is data for their tooling, and the reviewer did not ask."""
    exe = tmp_path / "anime.exe"
    exe.write_bytes(b"x")
    out = tmp_path / "m"
    assert main(["1.2.3", "--from-file", str(exe), "--out", str(out)]) == 0
    for path in (out / "winget").glob("*.yaml"):
        for line in path.read_text().splitlines():
            assert not line.lstrip().startswith("#"), f"{path.name}: {line}"


def test_stripping_comments_leaves_a_hash_inside_a_value_alone():
    """A `#` mid-line is part of the value. The scoop manifest uses one as a URL
    fragment to rename the downloaded file, and eating it would break the
    install for everyone."""
    from scripts.make_manifests import strip_comments

    kept = strip_comments('# a note\nurl: https://x/y.exe#/anime.exe\n')
    assert "#/anime.exe" in kept
    assert "a note" not in kept


def test_the_scoop_manifest_keeps_its_url_fragment(tmp_path):
    """End to end, because that fragment is what makes scoop name the binary
    `anime.exe` rather than `anime-sh-0.2.66-windows-x64.exe` — which is what
    the shims point at."""
    exe = tmp_path / "anime.exe"
    exe.write_bytes(b"x")
    out = tmp_path / "m"
    assert main(["1.2.3", "--from-file", str(exe), "--out", str(out)]) == 0
    data = json.loads((out / "scoop" / "anime-sh.json").read_text())
    assert data["architecture"]["64bit"]["url"].endswith("#/anime.exe")


def test_mpv_is_a_hard_dependency_in_both_manifests():
    """`scoop install anime-sh` and `winget install ...` have to be the whole
    job. anime-sh hands the stream to mpv — without it the app installs,
    launches, and fails at the exact moment you press Enter on an episode, which
    is the worst possible place to discover a missing dependency.

    Declared rather than bundled: mpv is 116 MB and GPL, so shipping the binary
    inside an MIT release would also make us responsible for offering its
    source.
    """
    yaml = pytest.importorskip("yaml")

    scoop = json.loads((ROOT / "packaging" / "scoop" / "anime-sh.json").read_text())
    # Bucket-qualified on purpose. mpv is not in scoop's `main` bucket, and a
    # freshly installed scoop has only `main` - so a bare "mpv" aborted the
    # install with `Couldn't find manifest for 'mpv'` and no clue which bucket
    # to add. Qualifying it makes scoop name the missing bucket instead, and the
    # README adds `extras` before it gets that far.
    assert scoop.get("depends") == "extras/mpv", (
        "scoop would install anime-sh without mpv, or fail without saying why"
    )

    installer = yaml.safe_load(
        (ROOT / "packaging" / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    )
    deps = installer["Dependencies"]["PackageDependencies"]
    assert [d["PackageIdentifier"] for d in deps] == ["shinchiro.mpv"]


def test_ffmpeg_is_not_forced_on_people_who_only_stream():
    """It is only needed by `anime download`, and the usual Windows build is
    several hundred megabytes — 706 MB in the one scoop ships. Making it
    mandatory would charge every viewer for a feature most never use."""
    yaml = pytest.importorskip("yaml")

    scoop = json.loads((ROOT / "packaging" / "scoop" / "anime-sh.json").read_text())
    assert "ffmpeg" not in str(scoop.get("depends", "")), "ffmpeg became mandatory"
    assert "ffmpeg" in scoop.get("suggest", {}), "ffmpeg is not even mentioned"

    installer = yaml.safe_load(
        (ROOT / "packaging" / "winget" / "AnimeshSharma.anime-sh.installer.yaml").read_text()
    )
    ids = [d["PackageIdentifier"]
           for d in installer["Dependencies"]["PackageDependencies"]]
    assert not any("ffmpeg" in i.lower() for i in ids)


def test_the_readme_adds_the_bucket_mpv_actually_lives_in():
    """The install instructions have to work on a machine that has nothing.
    `mpv` is in scoop's `extras` bucket; a scoop installed a minute ago has only
    `main`, so without this line `scoop install anime-sh` aborts on the
    dependency - on exactly the clean machine the instructions are written for.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # Every place we tell someone to install must have added `extras` first.
    parts = readme.split("scoop install anime-sh")
    assert len(parts) == 3, "the number of install blocks changed"
    for preceding in parts[:-1]:
        assert "scoop bucket add extras" in preceding, (
            "an install block does not add the bucket mpv lives in"
        )


# -- chocolatey -------------------------------------------------------------- #
def test_the_chocolatey_package_depends_on_the_maintained_mpv():
    """`mpvio`, not `mpv`.

    The community package literally named `mpv` is titled "[Deprecated] mpv";
    its own description says it is deprecated in favour of `mpvio`, and it is
    pinned to a 2024 build. Depending on the obvious name would install a stale
    player — the same shape of mistake as depending on scoop's `mpv` without
    noticing it lives in the `extras` bucket.
    """
    import xml.etree.ElementTree as ET

    ns = {"n": "http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd"}
    root = ET.parse(ROOT / "packaging" / "chocolatey" / "anime-sh.nuspec").getroot()
    deps = [d.get("id") for d in root.findall(".//n:dependency", ns)]
    assert deps == ["mpvio"], f"chocolatey dependencies are {deps}"


def test_chocolatey_does_not_force_ffmpeg_on_people_who_only_stream():
    """Same reasoning as the other two channels: it is only needed by
    `anime download`, and the Windows build is several hundred megabytes."""
    import xml.etree.ElementTree as ET

    ns = {"n": "http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd"}
    root = ET.parse(ROOT / "packaging" / "chocolatey" / "anime-sh.nuspec").getroot()
    deps = [d.get("id", "").lower() for d in root.findall(".//n:dependency", ns)]
    assert not any("ffmpeg" in d for d in deps)
    # But it must still be mentioned, with the command to get it.
    text = (ROOT / "packaging" / "chocolatey" / "anime-sh.nuspec").read_text()
    assert "choco install ffmpeg" in text


def test_the_chocolatey_install_script_is_generated_never_typed(tmp_path):
    """The URL and the checksum are the two fields that must match an exact
    published file. Chocolatey verifies the checksum at install time, so a wrong
    one fails for every user and the fix is another moderated submission."""
    out, expected = _render(tmp_path, version="4.5.6")
    script = (out / "chocolatey" / "tools" / "chocolateyinstall.ps1").read_text()
    assert expected in script
    assert PLACEHOLDER_HASH not in script
    assert "releases/download/v4.5.6/anime-sh-4.5.6-windows-x64.exe" in script

    nuspec = (out / "chocolatey" / "anime-sh.nuspec").read_text()
    assert "<version>4.5.6</version>" in nuspec
    assert PLACEHOLDER_VERSION not in nuspec


def test_the_chocolatey_package_downloads_the_same_artifact_as_everyone_else():
    """One artifact per release for every channel to verify against. If this
    URL drifts from the one the release actually publishes, chocolatey installs
    404 while the release itself looks green."""
    script = (ROOT / "packaging" / "chocolatey" / "tools"
              / "chocolateyinstall.ps1").read_text()
    assert "anime-sh-0.0.0-windows-x64.exe" in script, (
        "not pointing at the versioned release asset"
    )
    # Shimmed as `anime`, which is the command every doc tells people to run.
    assert "'anime.exe'" in script


def test_the_release_installs_the_chocolatey_package_rather_than_only_packing_it():
    """`choco pack` proves the XML parses. It does not prove the download URL
    resolves, the checksum matches, the shim is created, or that the declared
    mpv dependency exists under the name we guessed."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())

    assert "chocolatey" in wf["jobs"], "nothing builds the chocolatey package"
    job = wf["jobs"]["chocolatey"]
    # After the release exists: the install script downloads the published
    # binary by URL, so there is nothing to install before then.
    assert job["needs"] == "github-release" or "github-release" in job["needs"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "choco install anime-sh" in run, "packs but never installs"
    assert "--version" in run, "installs but never runs the result"
    assert "mpvio" in run, "never checks the dependency actually landed"


def test_the_chocolatey_package_satisfies_the_repositorys_own_rules():
    """The community repository runs a validator, and its Requirements block
    approval until fixed. These are checked against the rules as implemented in
    chocolatey's package-validator, not from memory — the two that looked most
    likely to bite turned out not to, and it would have been easy to "fix" them
    into something worse.
    """
    import re
    import xml.etree.ElementTree as ET

    ns = {"n": "http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd"}
    src = ROOT / "packaging" / "chocolatey" / "anime-sh.nuspec"
    meta = ET.parse(src).getroot().find("n:metadata", ns)

    def field(name: str) -> str:
        node = meta.find(f"n:{name}", ns)
        return (node.text or "").strip() if node is not None else ""

    # ProjectUrlRequirement, LicenseUrlValidRequirement,
    # PackageSourceUrlValidRequirement — each must be present and resolvable.
    for name in ("projectUrl", "licenseUrl", "packageSourceUrl", "description",
                 "authors", "id", "version"):
        assert field(name), f"{name} is required and missing"
    for name in ("projectUrl", "licenseUrl", "packageSourceUrl"):
        assert field(name).startswith("https://"), f"{name} is not https"

    # DescriptionHeadingMarkdownRequirement: `###Heading` with no space after
    # the hashes is what it rejects. `### Heading` is fine.
    assert not re.search(r"^(#+)([^\s#].*)$", field("description"), re.M), (
        "the description contains a heading the validator calls invalid"
    )
    # DescriptionWordCountMinimum30Guideline / Maximum4000Requirement.
    words = len(field("description").split())
    assert 30 <= words <= 4000, f"description is {words} words"


def test_the_chocolatey_scripts_satisfy_the_script_requirements():
    """Rules that reject on the *contents* of the automation scripts."""
    import re

    tools = ROOT / "packaging" / "chocolatey" / "tools"
    names = sorted(p.name for p in tools.glob("*.ps1"))
    # InstallScriptsNamedCorrectlyRequirement — chocolatey only runs these
    # exact names, so a typo produces a package that installs nothing at all.
    assert names == ["chocolateyinstall.ps1", "chocolateyuninstall.ps1"], names

    for path in tools.glob("*.ps1"):
        body = path.read_text(encoding="utf-8")
        # ScriptsDoNotContainChocoCommandsRequirement — a package that shells
        # out to choco during its own install deadlocks the run.
        assert not re.search(r"\bchoco(\.exe)?\s+\w", body), path.name
        # CommentsShouldBeCleanedUpRequirement looks for the template's own
        # boilerplate left behind.
        assert "main helper" not in body.lower(), path.name
        # ScriptsDoNotContainImportOfChocolateyModuleRequirement.
        assert "import-module chocolatey" not in body.lower(), path.name
