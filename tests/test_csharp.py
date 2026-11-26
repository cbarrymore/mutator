"""C# mutation sites, source selection, and dotnet test runs."""

import shutil
from pathlib import Path

import pytest

from mutator.cli import parse_args, run, select_files
from mutator.functions import sites_in_file
from mutator.runner import CommandRunner
from mutator.runner import test_plan as plan_command

SOURCE = """namespace Demo.Billing;

public class Board
{
    [Range(0, 1)]
    public int Place(int x, bool ready, string? name)
    {
        var y = -x + 1 * 2 / 3;
        if (!ready && x >= 0 || x != 1 && x <= 4 && x < 5 && x == 6)
        {
            return y > 0 ? x - 1 : 0;
        }
        var label = name ?? "a > 0 && true";
        var size = name!.Length;
        var items = new List<int>();
        // x > 0 || false
        return x is > 0 and < 9 ? size : (true ? 1 : 0);
    }

    int Hidden() => 1;

    private bool Closed() { return false; }

    [Route("api/[controller]")]
    [HttpGet("{id}")]
    public int Get(int id) => id + 1;

    protected internal int Shared() => 0;
}
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sites(tmp_path: Path, source: str = SOURCE, relative: str = "src/Demo/Board.cs"):
    path = _write(tmp_path / relative, source)
    return sites_in_file(source, path, tmp_path, relative)


def _pairs(sites):
    return {(site.original, site.mutant) for site in sites}


def test_csharp_uses_the_java_operator_set(tmp_path):
    pairs = _pairs(_sites(tmp_path))
    for pair in [
        ("+", "-"),
        ("-", "+"),
        ("*", "/"),
        ("/", "*"),
        (">", ">="),
        (">=", ">"),
        ("<", "<="),
        ("<=", "<"),
        ("==", "!="),
        ("!=", "=="),
        ("&&", "||"),
        ("||", "&&"),
        ("!", ""),
        ("-", ""),
        ("true", "false"),
        ("false", "true"),
        ("0", "1"),
        ("1", "0"),
    ]:
        assert pair in pairs, pair


def test_null_coalescing_patterns_generics_attributes_and_strings_are_left_alone(tmp_path):
    sites = _sites(tmp_path)
    pairs = _pairs(sites)
    assert not any(original in {"??", "and", "or"} for original, _ in pairs)
    lines = {site.line for site in sites}
    assert 5 not in lines  # [Range(0, 1)]
    assert 13 not in lines  # ?? and the string
    assert 14 not in lines  # null-forgiving !
    assert 15 not in lines  # List<int>
    assert 16 not in lines  # comment
    pattern_line = [site for site in sites if site.line == 17]
    assert {site.original for site in pattern_line} == {"true", "1", "0"}


def test_members_join_the_crapper_namespace_and_default_to_private(tmp_path):
    sites = _sites(tmp_path)
    forms = {site.form_id for site in sites}
    assert {site.namespace for site in sites} == {"Demo.Billing.Board"}
    assert "defn/Place" in forms
    assert "defn-/Hidden" in forms
    assert "defn-/Closed" in forms
    assert "defn/Get" in forms
    assert "defn/Shared" in forms


def test_csharp_test_projects_generated_files_and_obj_are_skipped(tmp_path, capsys):
    source = _write(tmp_path / "src" / "Demo" / "Board.cs", "class Board {}\n")
    nested_tests = _write(tmp_path / "src" / "Demo.UnitTests" / "BoardSpec.cs", "class S {}\n")
    named = _write(tmp_path / "src" / "Demo" / "BoardTests.cs", "class T {}\n")
    generated = _write(tmp_path / "src" / "Demo" / "Strings.Designer.cs", "class D {}\n")
    _write(tmp_path / "src" / "Demo" / "obj" / "Debug" / "Board.AssemblyInfo.cs", "class O {}\n")
    _write(tmp_path / "tests" / "Demo.Tests" / "BoardTest.cs", "class R {}\n")
    chosen = select_files(parse_args(["--root", str(tmp_path)]))
    assert chosen == [source.resolve()]
    explicit = select_files(
        parse_args(["--root", str(tmp_path), str(nested_tests), str(named), str(generated)])
    )
    assert explicit == []
    assert "Skipping test file" in capsys.readouterr().err


def test_dotnet_test_runs_the_solution_that_lists_the_project(tmp_path):
    _write(tmp_path / "Client.slnx", '<Solution><Project Path="src/Other/Other.csproj" /></Solution>')
    _write(
        tmp_path / "Service.slnx",
        '<Solution><Project Path="src/Demo/Demo.csproj" />'
        '<Project Path="tests/Demo.Tests/Demo.Tests.csproj" /></Solution>',
    )
    _write(tmp_path / "src" / "Demo" / "Demo.csproj", "<Project />")
    source = _write(tmp_path / "src" / "Demo" / "Board.cs", "class Board {}\n")
    command, cwd = plan_command(tmp_path, source, "csharp", None)
    assert command == ["dotnet", "test", "Service.slnx"]
    assert cwd == tmp_path.resolve()


def test_a_worker_builds_dotnet_output_inside_itself(tmp_path):
    worker = tmp_path / "target" / "mutation-workers" / "run-1" / "worker-0"
    worker.mkdir(parents=True)
    probe = "import os; print(os.environ.get('ArtifactsPath', ''))"
    inside = CommandRunner().run(["python", "-c", probe], worker, 10)
    outside = CommandRunner().run(["python", "-c", probe], tmp_path, 10)
    assert Path(inside.output.strip()) == worker / "target" / "dotnet"
    assert outside.output.strip() == ""


@pytest.mark.skipif(shutil.which("dotnet") is None, reason="dotnet is not installed")
def test_dotnet_compiles_each_mutant_in_its_worker(tmp_path, capsys):
    root = tmp_path / "svc"
    _write(
        root / "src" / "Calc" / "Calc.csproj",
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
    )
    source = _write(
        root / "src" / "Calc" / "Calc.cs",
        "namespace Demo;\npublic static class Calc\n{\n"
        "    public static int Add(int a, int b) => a + b;\n"
        "    public static bool IsBig(int x) => x > 10;\n}\n",
    )
    _write(
        root / "tests" / "Calc.Tests" / "Calc.Tests.csproj",
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup><ItemGroup>"
        '<ProjectReference Include="../../src/Calc/Calc.csproj" /></ItemGroup></Project>',
    )
    _write(
        root / "tests" / "Calc.Tests" / "Program.cs",
        "return Demo.Calc.Add(2, 3) == 5 && Demo.Calc.IsBig(11) ? 0 : 1;\n",
    )
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--max-workers",
            "2",
            "--root",
            str(root),
            "--test-command",
            "dotnet run --project tests/Calc.Tests",
            "src/Calc/Calc.cs",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3, captured.err
    text = (root / ".metrics" / "mutate" / "Demo.Calc.edn").read_text(encoding="utf-8")
    assert ':id "defn/Add"' in text and ":killed 1" in text
    assert ':id "defn/IsBig"' in text and ":survived 1" in text
    assert "a + b" in source.read_text(encoding="utf-8")
