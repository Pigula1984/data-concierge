"""Shared pytest configuration: tests never call a real model by default."""

import os

from pydantic_ai import models

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
models.ALLOW_MODEL_REQUESTS = False
