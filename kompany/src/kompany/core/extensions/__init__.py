"""Customer extension layer (07-24 four-layer update architecture).

Four ownership layers, each with its own writer:

1. **Immutable Core** — the installed release; read-only to the daemon
   (Stage C hardened unit) and never touched here.
2. **Vendor Pro** — entry-point plugins pinned by the release manifest.
3. **Customer evolution** — this package: extension packages a customer
   installs into ``<data_dir>/extensions/``, described by a manifest, kept
   in their own tables (``extensions``, ``extension_runs``) so a vendor
   release's migrations never touch them, and executed **out of process**
   with only manifest-declared capabilities.
4. **Proposal workspaces** — ``core/self_update`` clones; never imported by
   the running daemon.

Executable extensions require explicit founder approval before they run
(``extension_activate`` card); memories / skills / declarative workflows keep
evolving automatically under the constitution and are out of scope here.
"""

from kompany.core.extensions.manifest import (
    ExtensionManifest,
    ManifestError,
    core_compatible,
    load_manifest,
    package_hash,
)

__all__ = [
    "ExtensionManifest",
    "ManifestError",
    "core_compatible",
    "load_manifest",
    "package_hash",
]
