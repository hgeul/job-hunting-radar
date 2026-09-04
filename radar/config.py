# -*- coding: utf-8 -*-
"""프로필 config·이력 프로필 로딩."""

import json
import os

from radar.settings import HERE


def load_config(path="config.json"):
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_profile(cfg):
    path = os.path.join(HERE, cfg["output"]["profile_file"])
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
