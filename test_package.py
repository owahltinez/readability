from importlib.resources import files

import readability
from readability.checking import CheckReport, check_paths
from readability.cli import cli, main


def test_public_modules_are_importable() -> None:
    assert callable(check_paths)
    assert CheckReport.__module__ == "readability.checking"
    assert cli.name == "cli"
    assert callable(main)


def test_package_root_has_no_re_exports() -> None:
    assert not hasattr(readability, "check_paths")
    assert not hasattr(readability, "CheckReport")
    assert not hasattr(readability, "__all__")


def test_bundled_data_is_packaged_with_readability() -> None:
    package = files("readability")

    assert package.joinpath("configs", "ruff.toml").is_file()
    assert package.joinpath("guides", "pyguide.md").is_file()
