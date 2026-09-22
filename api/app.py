"""FireAI Pro — ASGI entry point (Procfile: ``uvicorn api.app:app``).

FireAI Pro 2.0, milestone 1: drawing understanding only.

The v1 application that previously lived in this file (design engine +
LLM orchestrator + drawing engine) has been retired from the served app; its
routes now return 410 Gone. See docs/FIREAI_AUDIT.md for why, and
docs/ARCHITECTURE_V2.md for the new pipeline. The v1 source is preserved in git
history and the legacy modules remain in the repository pending an approved
deletion list (docs/LEGACY_CODE.md).
"""

import logging

from fireai.api.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

app = create_app()
