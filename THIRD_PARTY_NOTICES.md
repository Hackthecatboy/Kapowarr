# Adapted integration code

Kapowarr is originally developed by Casvt and contributors and is licensed
under the GNU GPL version 3. The original license and notices remain in place.

The shared Newznab/Torznab protocol module and its namespace/enclosure and
capability-test scenarios draw from:

- Project: https://github.com/silasfelinus/Kapowarr
- Revision: `2a283b1d95ae0ad17d7a77acffab74424b8fb586`
- Source files: `backend/implementations/torznab.py`,
  `tests/Tbackend/torznab.py`
- License: GNU GPL version 3; see `LICENSE`.
- Adapted files: `backend/implementations/znab.py`,
  `tests/Tbackend/implementations/znab.py`
- Adaptation date: September 20, 2026.

The adaptations use Kapowarr v1.3.2 error definitions and separate protocol
operations from persistence and comic matching. They add explicit API-error
handling, pagination, bounded responses, capability validation, and local HTTP
fixture tests. This does not imply endorsement by either upstream project.


The SABnzbd and NZBGet adapter files under
`backend/implementations/external_clients/usenet/` adapt queue/history state
interpretation, split-size handling, and API argument conventions from
`backend/implementations/usenet_clients/SABnzbd.py` and `NZBGet.py` in the same
reference revision. Adapted September 20, 2026 under GPL version 3. This fork
uses its own v1.3.2 registries, bounded transport, stricter connection tests,
durable submission journal, and conservative copy import. The reference
fork's broader application changes were not merged.
