# Error Analysis - 2025-12-23

## Critical (Bugs)

### 1. Undefined variable in test
- **File:** `tests/test_interpreter.py:183`
- **Issue:** `F821 Undefined name 'accumulated_content'`
- **Fix:** Define or pass `accumulated_content` variable before use

## Code Quality (281 linting errors)

### Summary by Category

| Issue Code | Description | Count | Severity | Auto-fixable |
|------------|-------------|-------|----------|--------------|
| E402 | module-import-not-at-top-of-file | 80 | Low | No (often intentional) |
| I001 | unsorted-imports | 42 | Low | Yes |
| F401 | unused-import | 34 | Low | Yes |
| W291 | trailing-whitespace | 24 | Low | Yes |
| F841 | unused-variable | 23 | Medium | No |
| E721 | type-comparison | 14 | Medium | No |
| B007 | unused-loop-control-variable | 9 | Low | No |
| B904 | raise-without-from-inside-except | 7 | Medium | No |
| W293 | blank-line-with-whitespace | 6 | Low | Yes |
| C419 | unnecessary-comprehension-in-call | 5 | Low | No |
| F811 | redefined-while-unused | 5 | Medium | Yes |
| C401 | unnecessary-generator-set | 4 | Low | No |
| E712 | true-false-comparison | 4 | Low | No |
| E741 | ambiguous-variable-name | 3 | Low | No |
| UP015 | redundant-open-modes | 3 | Low | Yes |
| UP037 | quoted-annotation | 3 | Low | Yes |
| B012 | jump-statement-in-finally | 2 | Medium | No |
| E711 | none-comparison | 2 | Low | No |
| F541 | f-string-missing-placeholders | 2 | Low | Yes |
| F821 | undefined-name | 2 | High | No |
| W605 | invalid-escape-sequence | 2 | Medium | Yes |
| B006 | mutable-argument-default | 1 | Medium | No |
| B024 | abstract-base-class-without-abstract-method | 1 | Low | No |
| B027 | empty-method-without-abstract-decorator | 1 | Low | No |
| C416 | unnecessary-comprehension | 1 | Low | No |
| E722 | bare-except | 1 | Medium | No |

**Total:** 281 errors (87 auto-fixable with `ruff --fix`)

## Test Status

- **Validation tests (45):** All passing ✓
- **Full test suite:** Not fully verified (takes time)

## Recommended Actions

### Quick Wins (Auto-fix)
```bash
python -m ruff check . --fix
```
This will fix 87 issues automatically.

### Priority Fixes
1. Fix `tests/test_interpreter.py:183` - undefined variable bug
2. Fix B904 (raise-without-from) - 7 occurrences
3. Fix F841 (unused-variable) - 23 occurrences
4. Fix E721 (type-comparison) - 14 occurrences

### Low Priority
- E402 errors are mostly intentional (function definitions before imports)
- B007 unused loop variables can be renamed to `_var`

## Commands

```bash
# Check all issues
python -m ruff check .

# Auto-fix what's possible
python -m ruff check . --fix

# Check specific category
python -m ruff check . --select=F821

# Run tests
python -m pytest tests/ -v --tb=short
```
