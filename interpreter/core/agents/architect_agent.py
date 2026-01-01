"""
ArchitectAgent - Structural analysis and implementation design agent.

ARCHITECTURE: Analyzes codebases using AST parsing and pattern recognition.
Can collaborate with Scout to discover files when context is sparse.

WHY: Good architecture analysis requires knowing what files exist.
When Architect doesn't have file context, it asks Scout to explore first.

TRADEOFF: Scout queries add latency but ensure comprehensive analysis.

Capabilities:
- AST-based code analysis
- Dependency graph construction
- Pattern recognition
- Implementation planning
- Collaborative file discovery via Scout queries
"""

import ast
import os
import re
from dataclasses import dataclass, field

from .base_agent import AgentRole, BaseAgent, create_result
from .types import AgentResult


@dataclass
class CodeStructure:
    """Represents the structure of a code file."""

    file_path: str
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    lines_of_code: int = 0

    def summary(self) -> str:
        """Generate a brief summary."""
        parts = [f"**{self.file_path}** ({self.lines_of_code} lines)"]
        if self.classes:
            parts.append(f"  Classes: {', '.join(self.classes[:5])}")
        if self.functions:
            parts.append(f"  Functions: {', '.join(self.functions[:5])}")
        if self.imports:
            parts.append(f"  Imports: {len(self.imports)} modules")
        return "\n".join(parts)


@dataclass
class ImplementationPlan:
    """An implementation plan for a coding task."""

    task_summary: str
    approach: str
    files_to_modify: list[str] = field(default_factory=list)
    files_to_create: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    def to_context(self) -> str:
        """Format as context for other agents."""
        parts = [
            "## Implementation Plan",
            "",
            f"### Task: {self.task_summary}",
            "",
            "### Approach",
            self.approach,
            "",
        ]

        if self.files_to_modify:
            parts.append("### Files to Modify")
            for f in self.files_to_modify:
                parts.append(f"- {f}")
            parts.append("")

        if self.files_to_create:
            parts.append("### Files to Create")
            for f in self.files_to_create:
                parts.append(f"- {f}")
            parts.append("")

        if self.steps:
            parts.append("### Steps")
            for i, step in enumerate(self.steps, 1):
                parts.append(f"{i}. {step}")
            parts.append("")

        if self.risks:
            parts.append("### Risks")
            for risk in self.risks:
                parts.append(f"- ⚠️ {risk}")
            parts.append("")

        if self.dependencies:
            parts.append("### Dependencies")
            for dep in self.dependencies:
                parts.append(f"- {dep}")

        return "\n".join(parts)


class ArchitectAgent(BaseAgent):
    """
    Agent for analyzing code structure and designing implementation plans.

    Uses AST parsing and pattern recognition to understand codebases
    and produce actionable plans for other agents.
    """

    role = AgentRole.ARCHITECT

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: str | None = None,
        plugins=None,
        name: str | None = None,
    ):
        super().__init__(interpreter, memory, plugins=plugins, name=name)
        self.root_path = root_path or os.getcwd()

        # Cache for analyzed files
        self._structure_cache: dict[str, CodeStructure] = {}

        # File patterns to ignore
        self.ignore_patterns = {
            "__pycache__",
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "*.pyc",
        }

    def get_system_message(self) -> str:
        return """You are an Architect Agent specialized in code analysis and implementation design.

Your job is to:
1. Analyze code structure (classes, functions, dependencies)
2. Understand existing patterns and conventions
3. Design implementation approaches for tasks
4. Identify risks and dependencies

When analyzing:
- Look at imports and module structure
- Identify key abstractions and their relationships
- Note patterns that should be followed
- Consider backwards compatibility

When planning:
- Break complex tasks into steps
- Identify files that need changes
- Consider dependencies between changes
- Note potential risks or edge cases

Always output structured plans that other agents can follow."""

    def execute(self, task: str, context: str | None = None) -> "AgentResult":
        """
        Execute an architecture/planning task with optional Scout collaboration.

        ARCHITECTURE: If we need to analyze structure but don't have file
        context, ask Scout to explore first. This ensures comprehensive analysis.

        Args:
            task: The task description
            context: Optional context from Scout

        Returns:
            AgentResult with analysis or plan
        """
        self.log(f"Starting architecture task: {task[:50]}...")

        task_lower = task.lower()

        # Check if we need Scout to find files first
        if self._needs_file_discovery(task, context):
            context = self._discover_files_via_scout(task, context)

        try:
            if "analyze" in task_lower or "structure" in task_lower:
                # Code structure analysis
                return self._analyze_structure(task, context)

            elif (
                "plan" in task_lower
                or "design" in task_lower
                or "implement" in task_lower
            ):
                # Implementation planning
                return self._create_plan(task, context)

            elif "depend" in task_lower:
                # Dependency analysis
                return self._analyze_dependencies(task, context)

            else:
                # General architecture task - use LLM
                return self._general_analysis(task, context)

        except Exception as e:
            return create_result(
                role=self.role,
                success=False,
                content=f"Architecture analysis error: {str(e)}",
                error=str(e),
            )

    def _needs_file_discovery(self, task: str, context: str | None) -> bool:
        """
        Check if we need Scout to discover files first.

        WHY: Architecture analysis is more valuable when we know what
        files exist. If context is sparse, Scout can help.
        """
        # Can't collaborate if no orchestrator
        if not self.can_collaborate():
            return False

        # If we have substantial context with file references, we're good
        if context and len(context) > 300:
            return False

        # If task mentions specific concepts but no files, we need discovery
        concept_keywords = {
            "module",
            "component",
            "service",
            "handler",
            "controller",
            "model",
            "view",
            "api",
            "endpoint",
            "pipeline",
            "system",
        }

        task_lower = task.lower()
        has_concepts = any(kw in task_lower for kw in concept_keywords)

        # Check for file references in task
        file_refs = re.findall(r"[\w/\\]+\.\w+", task)
        has_files = bool(file_refs)

        # Need discovery if task mentions concepts but not specific files
        return has_concepts and not has_files

    def _discover_files_via_scout(self, task: str, existing_context: str | None) -> str:
        """
        Ask Scout to find relevant files for architecture analysis.

        ARCHITECTURE: Architect asks "find files related to X module",
        Scout returns relevant files, Architect analyzes them.
        """
        self.log("Asking Scout to discover relevant files...")

        try:
            from .types import AgentRole

            scout_query = f"Find all files related to: {task}"
            scout_result = self.ask_agent(AgentRole.SCOUT, scout_query)

            if scout_result.success and scout_result.content:
                new_context = f"## Scout Discovery\n{scout_result.content}"
                if existing_context:
                    return f"{existing_context}\n\n{new_context}"
                return new_context

        except Exception as e:
            self.log(f"Scout discovery failed: {e}")

        return existing_context or ""

    def analyze_file(self, file_path: str) -> CodeStructure | None:
        """
        Analyze a single file's structure.

        Args:
            file_path: Path to the file (relative or absolute)

        Returns:
            CodeStructure or None if analysis fails
        """
        # Check cache
        if file_path in self._structure_cache:
            return self._structure_cache[file_path]

        full_path = os.path.join(self.root_path, file_path)
        if not os.path.exists(full_path):
            return None

        try:
            with open(full_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            structure = CodeStructure(
                file_path=file_path,
                lines_of_code=len(content.splitlines()),
            )

            # Parse Python files with AST
            if file_path.endswith(".py"):
                self._analyze_python_ast(content, structure)
            else:
                # Basic analysis for other languages
                self._analyze_basic(content, structure)

            self._structure_cache[file_path] = structure
            return structure

        except Exception as e:
            self.log(f"Error analyzing {file_path}: {e}")
            return None

    def _analyze_python_ast(self, content: str, structure: CodeStructure) -> None:
        """Analyze Python code using AST."""
        try:
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    structure.classes.append(node.name)
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    # Only top-level functions
                    if hasattr(node, "col_offset") and node.col_offset == 0:
                        structure.functions.append(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        structure.imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        structure.imports.append(node.module)

            # Extract dependencies (external packages)
            for imp in structure.imports:
                root_module = imp.split(".")[0]
                if root_module not in {"os", "sys", "re", "json", "typing"}:
                    structure.dependencies.append(root_module)

            structure.dependencies = list(set(structure.dependencies))

        except SyntaxError:
            pass  # Fall back to basic analysis

    def _analyze_basic(self, content: str, structure: CodeStructure) -> None:
        """Basic analysis for non-Python files."""
        # Extract import-like statements
        import_patterns = [
            r"^import\s+[\w{}\s,]+\s+from\s+['\"]([^'\"]+)['\"]",  # JS/TS
            r"^from\s+[\w.]+\s+import",  # Python
            r"^require\(['\"]([^'\"]+)['\"]\)",  # Node.js
            r"^#include\s*[<\"]([^>\"]+)[>\"]",  # C/C++
        ]

        for pattern in import_patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            structure.imports.extend(matches)

        # Extract function/method definitions
        func_patterns = [
            r"^\s*def\s+(\w+)\s*\(",  # Python
            r"^\s*(?:async\s+)?function\s+(\w+)\s*\(",  # JS
            r"^\s*(?:public|private|protected)?\s*\w+\s+(\w+)\s*\(",  # Java/C++
        ]

        for pattern in func_patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            structure.functions.extend(matches)

        # Extract class definitions
        class_patterns = [
            r"^\s*class\s+(\w+)",  # Python/JS
            r"^\s*(?:public|private)?\s*class\s+(\w+)",  # Java
        ]

        for pattern in class_patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            structure.classes.extend(matches)

    def _analyze_structure(self, task: str, context: str | None) -> "AgentResult":
        """Analyze code structure based on task."""
        # Extract files to analyze from context or task
        files_to_analyze = []

        if context:
            # Look for file paths in context
            file_matches = re.findall(r"[\w/\\]+\.py\b", context)
            files_to_analyze.extend(file_matches)

        if not files_to_analyze:
            # Scan for Python files in root
            for root, dirs, files in os.walk(self.root_path):
                dirs[:] = [d for d in dirs if d not in self.ignore_patterns]
                for f in files:
                    if f.endswith(".py"):
                        rel_path = os.path.relpath(
                            os.path.join(root, f), self.root_path
                        )
                        files_to_analyze.append(rel_path)
                        if len(files_to_analyze) >= 50:
                            break
                if len(files_to_analyze) >= 50:
                    break

        # Analyze files
        structures = []
        for fp in files_to_analyze[:50]:
            structure = self.analyze_file(fp)
            if structure:
                structures.append(structure)

        # Format output
        content_parts = [
            "## Code Structure Analysis",
            f"Analyzed {len(structures)} files",
            "",
        ]

        total_classes = sum(len(s.classes) for s in structures)
        total_functions = sum(len(s.functions) for s in structures)
        total_lines = sum(s.lines_of_code for s in structures)

        content_parts.extend(
            [
                "### Overview",
                f"- Total files: {len(structures)}",
                f"- Total lines: {total_lines:,}",
                f"- Total classes: {total_classes}",
                f"- Total functions: {total_functions}",
                "",
                "### File Details",
            ]
        )

        for s in structures[:20]:
            content_parts.append(s.summary())
            content_parts.append("")

        if len(structures) > 20:
            content_parts.append(f"... and {len(structures) - 20} more files")

        return create_result(
            role=self.role,
            success=True,
            content="\n".join(content_parts),
            files_found=[s.file_path for s in structures],
            context_for_next="\n".join(content_parts),
            metadata={"structures": len(structures), "total_lines": total_lines},
        )

    def _create_plan(self, task: str, context: str | None) -> "AgentResult":
        """Create an implementation plan using LLM."""
        # Build planning prompt
        planning_prompt = f"""Analyze this task and create a detailed implementation plan.

Task: {task}

{f'Context from exploration:{chr(10)}{context}' if context else ''}

Please provide:
1. A brief summary of what needs to be done
2. The recommended approach
3. List of files that need to be modified
4. List of files that need to be created (if any)
5. Step-by-step implementation plan
6. Any risks or considerations
7. Dependencies or prerequisites

Format your response as a structured plan."""

        messages = self.prepare_messages(planning_prompt, context)
        response = self.run_interpreter(messages)

        # Parse the response into a plan structure
        plan = ImplementationPlan(
            task_summary=task[:100],
            approach=response,
        )

        # Extract files mentioned
        file_matches = re.findall(r"[\w/\\]+\.\w+\b", response)
        for f in file_matches:
            if "modify" in response.lower() and f in response:
                plan.files_to_modify.append(f)
            elif "create" in response.lower() and f in response:
                plan.files_to_create.append(f)

        plan.files_to_modify = list(set(plan.files_to_modify))[:10]
        plan.files_to_create = list(set(plan.files_to_create))[:10]

        return create_result(
            role=self.role,
            success=True,
            content=plan.to_context(),
            files_found=plan.files_to_modify + plan.files_to_create,
            context_for_next=plan.to_context(),
            metadata={"plan_type": "implementation"},
        )

    def _analyze_dependencies(self, task: str, context: str | None) -> "AgentResult":
        """Analyze project dependencies."""
        dependencies = {}
        dep_files = []

        # Check common dependency files
        dep_file_patterns = [
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "package.json",
            "Cargo.toml",
            "go.mod",
        ]

        for pattern in dep_file_patterns:
            for root, _, files in os.walk(self.root_path):
                for f in files:
                    if f == pattern:
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, self.root_path)
                        dep_files.append(rel_path)

                        try:
                            with open(full_path, encoding="utf-8") as file:
                                content = file.read()

                            if f == "requirements.txt":
                                deps = self._parse_requirements(content)
                            elif f == "pyproject.toml":
                                deps = self._parse_pyproject(content)
                            elif f == "package.json":
                                deps = self._parse_package_json(content)
                            else:
                                deps = []

                            dependencies[rel_path] = deps
                        except Exception:
                            pass
                break  # Don't recurse for these files

        content_parts = ["## Dependency Analysis", ""]

        for dep_file, deps in dependencies.items():
            content_parts.append(f"### {dep_file}")
            for dep in deps[:30]:
                content_parts.append(f"- {dep}")
            if len(deps) > 30:
                content_parts.append(f"... and {len(deps) - 30} more")
            content_parts.append("")

        return create_result(
            role=self.role,
            success=True,
            content="\n".join(content_parts),
            files_found=dep_files,
            context_for_next="\n".join(content_parts),
            metadata={"dependency_files": dep_files},
        )

    def _general_analysis(self, task: str, context: str | None) -> "AgentResult":
        """General architecture analysis using LLM."""
        messages = self.prepare_messages(task, context)
        response = self.run_interpreter(messages)

        return create_result(
            role=self.role,
            success=True,
            content=response,
            context_for_next=response,
        )

    def _parse_requirements(self, content: str) -> list[str]:
        """Parse requirements.txt format."""
        deps = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                # Remove version specifiers for display
                dep = re.split(r"[<>=!~\[]", line)[0].strip()
                if dep:
                    deps.append(dep)
        return deps

    def _parse_pyproject(self, content: str) -> list[str]:
        """Parse pyproject.toml dependencies."""
        deps = []
        # Simple parsing - look for dependencies section
        in_deps = False
        for line in content.splitlines():
            if "dependencies" in line.lower() and "=" in line:
                in_deps = True
                continue
            if in_deps:
                if line.startswith("[") and "dependencies" not in line.lower():
                    in_deps = False
                    continue
                match = re.search(r'["\']([^"\']+)["\']', line)
                if match:
                    dep = re.split(r"[<>=!~\[]", match.group(1))[0].strip()
                    if dep and not dep.startswith("#"):
                        deps.append(dep)
        return deps

    def _parse_package_json(self, content: str) -> list[str]:
        """Parse package.json dependencies."""
        import json

        deps = []
        try:
            data = json.loads(content)
            for key in ["dependencies", "devDependencies", "peerDependencies"]:
                if key in data:
                    deps.extend(data[key].keys())
        except json.JSONDecodeError:
            pass
        return deps
