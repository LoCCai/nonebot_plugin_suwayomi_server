# Development

Requires Python 3.10+ and NoneBot2 with the FastAPI driver for dashboard tests.

```sh
python -m pip install -e '.[test]' build
python -m pytest -q
python -m build
```

Keep runtime state outside the installed package. The package has only one root,
`nonebot_plugin_suwayomi_server/`; use relative imports inside it. Pure tests use
the `plugin_pkg` alias without executing plugin startup, and a subprocess test
checks real NoneBot loading. No generic `web`, `utils`, or `suwayomi` modules
should be put on `sys.path`.

Live tests require explicit `SUWAYOMI_LIVE_TESTS=1`, `SUWAYOMI_URL` and optional
authentication variables. They can modify the test server's library. Never point
them at a production library or commit credentials, downloads or local AI state.

The AstrBot v0.7.1 implementation remains in Git history. Preserve attribution
and AGPL-3.0 licensing when carrying changes across.
