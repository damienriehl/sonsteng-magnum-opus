#!/usr/bin/env bash
# DISABLED: direct PROD deploy bypasses the Publisher release ledger.
#
# Production is released only by tools/prod_release_daemon.py after a human
# Publisher authorizes an immutable batch. This historical command remains as a
# loud tripwire for old bookmarks/runbooks; it does not read credentials, build,
# deploy, or offer an override.
set -euo pipefail

echo "ERROR: direct production deployment is disabled." >&2
echo "Use the Publisher-authorized lane documented in docs/prod-release-operations.md." >&2
echo "Executor: tools/prod_release_daemon.py (config-off until its enablement gates pass)." >&2
exit 64
