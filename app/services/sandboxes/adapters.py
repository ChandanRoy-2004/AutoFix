import os
from pathlib import Path
import shutil
import sys

from app.services.sandboxes.base import BaseSandbox


class PythonSandbox(BaseSandbox):
    """Execution sandbox for Python test suites using pytest."""

    def write_files(self, workspace: Path, files: dict[str, str]) -> None:
        """Write Python source and test files to workspace."""
        workspace.mkdir(parents=True, exist_ok=True)
        for rel_path, content in files.items():
            target_path = workspace / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")

    def run_tests(self, workspace: Path, timeout: int = 30) -> tuple[bool, str]:
        """Execute pytest suite within the workspace."""
        cmd = [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        workspace_abs = str(workspace.resolve())
        env["PYTHONPATH"] = f"{workspace_abs}:{env.get('PYTHONPATH', '')}"
        return self._execute_command(cmd, cwd=workspace, timeout=timeout, env=env)

    def clean(self, workspace: Path) -> None:
        """Remove Python bytecode, __pycache__, and .pytest_cache artifacts."""
        if not workspace.exists():
            return
        for item in list(workspace.rglob("*")):
            try:
                if item.is_dir() and item.name in {".pytest_cache", "__pycache__"}:
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file() and item.suffix in {".pyc", ".pyo"}:
                    item.unlink(missing_ok=True)
            except Exception:
                pass


class CSharpSandbox(BaseSandbox):
    """Execution sandbox for .NET / C# test suites using dotnet test."""

    DEFAULT_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />
    <PackageReference Include="xunit" Version="2.6.6" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.5.6">
      <IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets>
      <PrivateAssets>all</PrivateAssets>
    </PackageReference>
  </ItemGroup>
</Project>
"""

    def write_files(self, workspace: Path, files: dict[str, str]) -> None:
        """Write C# source and test files to workspace, generating a default xUnit .csproj if none exists."""
        workspace.mkdir(parents=True, exist_ok=True)
        has_csproj = False
        for rel_path, content in files.items():
            target_path = workspace / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            if rel_path.endswith(".csproj"):
                has_csproj = True

        if not has_csproj:
            existing = list(workspace.glob("*.csproj"))
            if not existing:
                csproj_path = workspace / "AutoFixTests.csproj"
                csproj_path.write_text(self.DEFAULT_CSPROJ, encoding="utf-8")

    def run_tests(self, workspace: Path, timeout: int = 30) -> tuple[bool, str]:
        """Execute dotnet test in workspace."""
        cmd = ["dotnet", "test", "--logger", "console;verbosity=detailed"]
        return self._execute_command(cmd, cwd=workspace, timeout=timeout)

    def clean(self, workspace: Path) -> None:
        """Remove .NET build artifacts (bin/ and obj/ directories)."""
        if not workspace.exists():
            return
        for item in list(workspace.rglob("*")):
            try:
                if item.is_dir() and item.name in {"bin", "obj"}:
                    shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass


class JavaSandbox(BaseSandbox):
    """Execution sandbox for Java test suites using Maven or Gradle."""

    DEFAULT_POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.autofix</groupId>
    <artifactId>autofix-sandbox</artifactId>
    <version>1.0-SNAPSHOT</version>
    <properties>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.10.0</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>
        </plugins>
    </build>
</project>
"""

    def write_files(self, workspace: Path, files: dict[str, str]) -> None:
        """Write Java source and test files to workspace, generating a default pom.xml if needed."""
        workspace.mkdir(parents=True, exist_ok=True)
        has_build_file = False
        for rel_path, content in files.items():
            target_path = workspace / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            if rel_path in {"pom.xml", "build.gradle", "build.gradle.kts"}:
                has_build_file = True

        if not has_build_file:
            if not (workspace / "pom.xml").exists() and not (workspace / "build.gradle").exists():
                pom_path = workspace / "pom.xml"
                pom_path.write_text(self.DEFAULT_POM, encoding="utf-8")

    def run_tests(self, workspace: Path, timeout: int = 30) -> tuple[bool, str]:
        """Execute Maven or Gradle test runner in workspace."""
        if (workspace / "mvnw").exists():
            cmd = ["./mvnw", "test", "-B"]
        elif (workspace / "gradlew").exists():
            cmd = ["./gradlew", "test"]
        elif (workspace / "build.gradle").exists() or (workspace / "build.gradle.kts").exists():
            cmd = ["gradle", "test"]
        else:
            cmd = ["mvn", "test", "-B"]
        return self._execute_command(cmd, cwd=workspace, timeout=timeout)

    def clean(self, workspace: Path) -> None:
        """Remove Java build artifacts (target/, build/, .gradle/, and .class files)."""
        if not workspace.exists():
            return
        for item in list(workspace.rglob("*")):
            try:
                if item.is_dir() and item.name in {"target", "build", ".gradle"}:
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file() and item.suffix == ".class":
                    item.unlink(missing_ok=True)
            except Exception:
                pass


def get_sandbox(language: str) -> BaseSandbox:
    """Factory function returning the appropriate BaseSandbox implementation for a language."""
    lang = language.lower().strip()
    if lang in ["csharp", "c#", "dotnet"]:
        return CSharpSandbox()
    elif lang == "java":
        return JavaSandbox()
    return PythonSandbox()  # Default to Python
