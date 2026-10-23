"""Parse source and verify internal imports without importing optional services."""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {'dashboard', 'database', 'services', 'sources', 'api', 'tools', 'pokemon_eras'}


def check_repository(root=ROOT):
    root = Path(root)
    problems = []
    exports = {}

    def has_symbol(module_path, name):
        if name == '*':
            return True
        if module_path not in exports:
            tree = ast.parse(module_path.read_text(encoding='utf-8'))
            names = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update(x.asname or x.name.split('.')[0] for x in node.names)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        names.update(x.id for x in ast.walk(target) if isinstance(x, ast.Name))
            exports[module_path] = names
        return name in exports[module_path]
    for path in sorted(root.rglob('*.py')):
        if any(p in {'.venv', 'venv', '.git', '__pycache__'} for p in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except (OSError, SyntaxError) as error:
            problems.append(f'{path.relative_to(root)}: {error}')
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split('.')[0] not in PACKAGES:
                    continue
                target = root / name.replace('.', '/')
                module = target.with_suffix('.py') if target.with_suffix('.py').is_file() else target / '__init__.py'
                if not module.is_file():
                    problems.append(f'{path.relative_to(root)}: missing local module {name}')
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        child = target / alias.name
                        if not has_symbol(module, alias.name) and not child.with_suffix('.py').is_file() and not (child / '__init__.py').is_file():
                            problems.append(f'{path.relative_to(root)}: missing imported name {name}.{alias.name}')
    for name in ['card_rank_v3.json', 'card_rank_v4_hybrid.json', 'model_lockbox_policy.json']:
        if not (root / 'models' / name).is_file():
            problems.append(f'Missing model specification: {name}')
    return problems


def main():
    errors = check_repository()
    for error in errors:
        print('FAIL:', error)
    if not errors:
        print('Python syntax, internal modules/imported names and required model files: OK')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
