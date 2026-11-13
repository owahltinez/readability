from pathlib import Path

from readability.tools import _get_tool_definitions


def _plans(path: Path, root: Path, name: str) -> list:
    return [p for p in _get_tool_definitions(path, root) if p.name == name]


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_tsc_checks_the_project_that_owns_the_tsconfig(
    tmp_path: Path,
) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "app.ts"
    source.touch()
    tsc = _executable(tmp_path / "node_modules" / ".bin" / "tsc")

    [plan] = _plans(source, tmp_path, "tsc")

    assert plan.check == (str(tsc), "--noEmit", "-p", str(tmp_path.resolve()))
    assert plan.format == plan.fix == plan.check_format == ()


def test_tsc_does_not_run_without_a_tsconfig(tmp_path: Path) -> None:
    (tmp_path / "app.ts").touch()

    assert _plans(tmp_path, tmp_path, "tsc") == []


def test_tsc_is_found_where_a_workspace_hoisted_it(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "web"
    package.mkdir(parents=True)
    (package / "tsconfig.json").write_text("{}")
    (package / "app.ts").touch()
    tsc = _executable(tmp_path / "node_modules" / ".bin" / "tsc")

    [plan] = _plans(package, tmp_path, "tsc")

    assert plan.check[0] == str(tsc)


def test_tsc_checks_each_nested_project_once(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "a.ts").touch()
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "tsconfig.json").write_text("{}")
    (tmp_path / "web" / "b.ts").touch()
    (tmp_path / "web" / "c.tsx").touch()

    projects = sorted(p.check[-1] for p in _plans(tmp_path, tmp_path, "tsc"))

    assert projects == sorted(
        [str(tmp_path.resolve()), str((tmp_path / "web").resolve())]
    )


def test_go_vet_checks_every_package_below_a_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "cmd").mkdir()
    (tmp_path / "cmd" / "main.go").touch()

    [plan] = _plans(tmp_path / "cmd", tmp_path, "go vet")

    assert plan.check == ("go", "vet", "./...")
    assert plan.cwd == (tmp_path / "cmd").resolve()


def test_go_vet_checks_a_files_whole_package(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    source = tmp_path / "main.go"
    source.touch()

    [plan] = _plans(source, tmp_path, "go vet")

    assert plan.check == ("go", "vet", ".")
    assert plan.cwd == tmp_path.resolve()


def test_go_vet_does_not_run_outside_a_module(tmp_path: Path) -> None:
    (tmp_path / "main.go").touch()

    assert _plans(tmp_path, tmp_path, "go vet") == []


def test_go_vet_runs_inside_each_nested_module(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/t\n")
    (tmp_path / "tools" / "gen.go").touch()

    [plan] = _plans(tmp_path, tmp_path, "go vet")

    assert plan.check == ("go", "vet", "./...")
    assert plan.cwd == (tmp_path / "tools").resolve()


def test_go_vet_skips_a_module_whose_go_is_all_nested(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/t\n")
    (tmp_path / "tools" / "gen.go").touch()

    [plan] = _plans(tmp_path, tmp_path, "go vet")

    assert plan.cwd == (tmp_path / "tools").resolve()
