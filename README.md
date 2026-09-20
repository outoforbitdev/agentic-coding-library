# agentic-coding-library

<p>
  <a href="https://github.com/outoforbitdev/agentic-coding-library/releases/latest">
    <img alt="Latest github release" src="https://img.shields.io/github/v/release/outoforbitdev/agentic-coding-library?logo=github">
  </a>
</p>

Personal Claude Code plugin marketplace (`outoforbitdev`).

## Plugins

### agentic-coding-library

- **pull-issue** — fetch a GitHub issue and save it locally as Markdown.
- **pr-template** — find and correctly follow a repo's PR template when opening a PR.

## Installing

```
/plugin marketplace add outoforbitdev/agentic-coding-library
/plugin install agentic-coding-library@outoforbitdev
```

## Releasing

Releases are driven by `CHANGELOG.md`. Add a new version heading (for example
`## 0.3.0`) at the top of the changelog and bump the plugin version in
`plugins/agentic-coding-library/.claude-plugin/plugin.json`. When the change
lands on `main`, the release workflow publishes a GitHub release with that
version's notes.
