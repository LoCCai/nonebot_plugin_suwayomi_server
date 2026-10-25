# NoneBot2 migration, 0.8.0

This release ports FFFold's AstrBot plugin v0.7.1 into a distributable NoneBot2
package. The original implementation and its documentation remain available in
the Git history before this migration.

The port includes source listing, parallel search and ranking, chapter lists,
reading, ZIP/PDF/CBZ download, subscriptions, scheduled update notifications,
optional chapter delivery, result cards and a token-protected management UI.
AstrBot's framework-specific Agent FunctionTools are not included. NoneBot uses
the same `/漫画` command family instead.

There is one Python package and no top-level `utils`, `web` or `suwayomi` import
namespace. `pyproject.toml` builds a wheel containing the package, dashboard
assets and license, without runtime state, tests or local workspace material.

Configuration comes from NoneBot's `SUWAYOMI_*` settings, then the data
directory's optional `config.json`. State files use private atomic replacement;
corrupt subscription JSON fails rather than being silently replaced by empty
state. Downloads and rendered cards default to the data directory's `cache/`.
Image messages contain bytes; uploaded files require a shared path between the
Bot and the QQ gateway. No mount of the renderer's temporary directory is needed.

Management APIs are disabled until a dashboard token is configured. The public
static shell accepts a token in a password field, stores it in the current tab
and sends Bearer headers. Query-string tokens are rejected. Global manual update
is restricted to NoneBot superusers.

Recipient progress advances after confirmed notification and optional chapter
delivery. Offline recipients remain pending and retry on the next check, while
successful recipients retain their own checkpoint. An interrupted send can
still be delivered twice; this is not an exactly-once protocol. A failed chapter
batch may be retried in full. NoneBot session keys must be mapped explicitly
when importing AstrBot state.

Validation on Python 3.12: **481 passed**, 46 opt-in live cases skipped.
Coverage includes real isolated NoneBot loading, a built-wheel loading check,
remote image transport, literal CQ text, API authentication, private state and
failed-recipient retry. A Starlette warning concerns the test client's HTTPX
integration; production dependencies were not upgraded to silence it.

The Compose example pins Suwayomi v2.4.2366 and its amd64 manifest digest,
persists its own data and limits the host port to loopback. Installing extensions
and choosing trusted manga sources is a separate operator step; a fresh server
has no external manga sources or library.
