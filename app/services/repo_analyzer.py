import ast
from collections import deque
import logging
import os
from pathlib import Path
import re

logger = logging.getLogger(__name__)


class RepoAnalyzer:
    """Static analyzer for extracting AST/regex dependency graphs and localized context from repositories."""

    LANGUAGE_EXTENSIONS = {
        "python": [".py"],
        "py": [".py"],
        "csharp": [".cs"],
        "c#": [".cs"],
        "dotnet": [".cs"],
        "java": [".java"],
    }

    def scan_python_dependencies(self, file_path: Path) -> set[str]:
        """Parse Python source code using AST and extract imported module names and symbols."""
        if not file_path.exists() or not file_path.is_file():
            return set()

        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError, OSError) as e:
            logger.debug("Failed to parse Python AST for %s: %s", file_path, e)
            return set()

        dependencies: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name:
                        dependencies.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    dependencies.add(module)
                for alias in node.names:
                    if alias.name and alias.name != "*":
                        if module:
                            dependencies.add(f"{module}.{alias.name}")
                        dependencies.add(alias.name)

        return dependencies

    def scan_csharp_dependencies(self, file_path: Path) -> set[str]:
        """Extract namespaces and types from C# source code using regex heuristics."""
        if not file_path.exists() or not file_path.is_file():
            return set()

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.debug("Failed to read C# file %s: %s", file_path, e)
            return set()

        dependencies: set[str] = set()

        # Match using directives: using System.Text; using MyApp.Models; using static System.Math;
        using_pattern = r"^\s*using\s+(?:static\s+)?(?:[\w\.]+\s*=\s*)?([A-Za-z0-9_\.]+)\s*;"
        for match in re.finditer(using_pattern, content, re.MULTILINE):
            dep = match.group(1).strip()
            if dep:
                dependencies.add(dep)
                if "." in dep:
                    dependencies.add(dep.split(".")[-1])

        # Match class instantiation patterns: new OrderService( or new User {
        instantiation_pattern = r"\bnew\s+([A-Za-z0-9_]+)\s*[\(\{]"
        for match in re.finditer(instantiation_pattern, content):
            dep = match.group(1).strip()
            if dep:
                dependencies.add(dep)

        return dependencies

    def scan_java_dependencies(self, file_path: Path) -> set[str]:
        """Extract packages and imported classes from Java source code using regex heuristics."""
        if not file_path.exists() or not file_path.is_file():
            return set()

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.debug("Failed to read Java file %s: %s", file_path, e)
            return set()

        dependencies: set[str] = set()

        # Match import statements: import com.example.models.User; import com.example.utils.*;
        import_pattern = r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_\.]+)(?:\.\*)?\s*;"
        for match in re.finditer(import_pattern, content, re.MULTILINE):
            dep = match.group(1).strip()
            if dep:
                dependencies.add(dep)
                if "." in dep:
                    dependencies.add(dep.split(".")[-1])

        return dependencies

    def build_dependency_graph(self, repo_dir: Path, language: str = "python") -> dict[str, list[str]]:
        """Recursively scan repo_dir for matching source files and map relative paths to internal dependencies."""
        repo_path = Path(repo_dir).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            return {}

        lang_key = (language or "python").lower().strip()
        extensions = self.LANGUAGE_EXTENSIONS.get(lang_key, [".py"])

        # Collect all source files
        all_files: list[Path] = []
        for ext in extensions:
            all_files.extend(repo_path.rglob(f"*{ext}"))

        # Filter out hidden or build/cache dirs
        ignored_dirs = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "bin", "obj", "target", ".gradle", "node_modules"}
        valid_files: list[Path] = [
            f for f in all_files
            if f.is_file() and not any(part in ignored_dirs for part in f.relative_to(repo_path).parts)
        ]

        # Map file paths and identifiers for lookup
        rel_files = [f.relative_to(repo_path).as_posix() for f in valid_files]
        file_map: dict[str, Path] = {rel: f for rel, f in zip(rel_files, valid_files)}

        # Build lookup tables
        stem_map: dict[str, list[str]] = {}
        module_notation_map: dict[str, str] = {}

        for rel in rel_files:
            p = Path(rel)
            stem = p.stem
            stem_map.setdefault(stem, []).append(rel)

            # Module dot notation, e.g. app/models/schemas.py -> app.models.schemas
            module_dot = rel.replace("/", ".").replace("\\", ".")
            for ext in extensions:
                if module_dot.endswith(ext):
                    module_dot = module_dot[:-len(ext)]
                    break
            module_notation_map[module_dot] = rel

        graph: dict[str, list[str]] = {}

        for rel, path_obj in file_map.items():
            if lang_key in ["csharp", "c#", "dotnet"]:
                raw_deps = self.scan_csharp_dependencies(path_obj)
            elif lang_key == "java":
                raw_deps = self.scan_java_dependencies(path_obj)
            else:
                raw_deps = self.scan_python_dependencies(path_obj)

            matched_rel_files: set[str] = set()

            for dep in raw_deps:
                # 1. Exact or suffix match in module notation (e.g. app.models.schemas)
                if dep in module_notation_map:
                    matched = module_notation_map[dep]
                    if matched != rel:
                        matched_rel_files.add(matched)
                else:
                    for mod_key, mod_rel in module_notation_map.items():
                        if mod_key.endswith(f".{dep}") or mod_key == dep:
                            if mod_rel != rel:
                                matched_rel_files.add(mod_rel)

                # 2. Stem match (e.g. schemas -> app/models/schemas.py)
                if dep in stem_map:
                    for matched in stem_map[dep]:
                        if matched != rel:
                            matched_rel_files.add(matched)

                # 3. Path conversion (e.g. app/models/schemas)
                as_path_str = dep.replace(".", "/")
                for ext in extensions:
                    candidate = f"{as_path_str}{ext}"
                    if candidate in file_map and candidate != rel:
                        matched_rel_files.add(candidate)

            graph[rel] = sorted(matched_rel_files)

        return graph

    def extract_relevant_context(
        self,
        repo_dir: Path,
        failing_file: str,
        language: str = "python",
        depth: int = 1,
    ) -> dict[str, str]:
        """Extract contents of the failing file and its connected upstream/downstream dependencies up to depth."""
        repo_path = Path(repo_dir).resolve()
        if not repo_path.exists():
            return {}

        graph = self.build_dependency_graph(repo_path, language=language)

        # Normalize failing_file relative path
        failing_path = Path(failing_file)
        if failing_path.is_absolute():
            try:
                norm_failing = failing_path.resolve().relative_to(repo_path).as_posix()
            except ValueError:
                norm_failing = failing_path.name
        else:
            norm_failing = failing_path.as_posix()

        # Find matching key in graph if partial path or filename was provided
        matched_failing_key: str | None = None
        if norm_failing in graph:
            matched_failing_key = norm_failing
        else:
            for k in graph:
                if k == norm_failing or k.endswith(f"/{norm_failing}") or Path(k).name == norm_failing:
                    matched_failing_key = k
                    break

        # Build reverse dependency graph (downstream dependents)
        reverse_graph: dict[str, list[str]] = {k: [] for k in graph}
        for src, targets in graph.items():
            for tgt in targets:
                if tgt in reverse_graph:
                    reverse_graph[tgt].append(src)

        selected_files: set[str] = set()

        if matched_failing_key:
            # BFS traversal up to depth
            queue: deque[tuple[str, int]] = deque([(matched_failing_key, 0)])
            selected_files.add(matched_failing_key)

            while queue:
                current_node, current_depth = queue.popleft()
                if current_depth < depth:
                    neighbors = set(graph.get(current_node, [])) | set(reverse_graph.get(current_node, []))
                    for nbr in neighbors:
                        if nbr not in selected_files:
                            selected_files.add(nbr)
                            queue.append((nbr, current_depth + 1))
        else:
            # If failing file is not in graph but exists on disk, include it directly
            direct_file = (repo_path / norm_failing).resolve()
            if direct_file.exists() and direct_file.is_file():
                selected_files.add(norm_failing)

        # Read file contents
        context: dict[str, str] = {}
        for rel_file in sorted(selected_files):
            file_disk_path = repo_path / rel_file
            if file_disk_path.exists() and file_disk_path.is_file():
                try:
                    content = file_disk_path.read_text(encoding="utf-8", errors="replace")
                    context[rel_file] = content
                except OSError as e:
                    logger.warning("Could not read file %s: %s", file_disk_path, e)

        return context
