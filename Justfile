# Bootstrap: one-time repository initialization
bootstrap:
    pre-commit install --hook-type commit-msg --hook-type pre-commit

# Install: no external dependencies (plain Markdown/JSON content)
install:
    @echo "No dependencies to install."

# Test: validate plugin/marketplace JSON manifests and skill frontmatter
test:
    python3 scripts/validate_manifests.py

# Lint: this repo has no npm/pip package manifest, so there is no
# language-specific linter to run. We use prettier (via npx, no local
# install required) to enforce consistent Markdown/JSON formatting, since
# that is real, meaningful enforcement for a skills-and-manifests-only repo
# rather than a no-op placeholder.
lint:
    npx --yes prettier --check "**/*.{md,json}"

lint-write:
    npx --yes prettier --write "**/*.{md,json}"

gate: test lint
