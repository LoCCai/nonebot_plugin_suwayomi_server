# Suwayomi for NoneBot2

This is a distributable NoneBot2 / OneBot v11 plugin, ported from
FFFold/astrbot_plugin_suwayomi_server v0.7.1. Preserve upstream attribution and
AGPL-3.0 licensing. The Git repository root contains pyproject.toml; the Python
package is the single nonebot_plugin_suwayomi_server/ directory below it.

- All changes use package-relative imports. Do not inject the package directory
  into sys.path or publish generic top-level utils, web or suwayomi modules.
- Keep credentials and state outside the Python package. Downloads and rendered
  cards belong under the configured data directory; never depend on a remote
  renderer or QQ gateway sharing the Bot's temporary directory.
- Dashboard APIs require a configured bearer token; an empty token disables the
  dashboard. Never log credentials or return exception strings to clients.
- An unavailable Bot or a failed send is a delivery failure, not success.
- Run pytest tests/ with the declared test extra; live tests require explicit
  environment configuration. Do not send real messages in unit tests.
- Build and inspect a wheel and test plugin loading in a separate NoneBot
  process before installing. Do not start a second production Bot.
- .openclaw/ holds local migration evidence and the preserved initial workspace;
  it must never be committed or included in distributions.
- Production lifecycle and installation follow the parent qq/AGENTS.md.
