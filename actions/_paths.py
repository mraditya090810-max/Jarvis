"""
actions/_paths.py — backward-compatible re-exports.

Canonical implementation lives in core.paths; existing imports from
actions._paths continue to work unchanged.
"""
from core.paths import clear_cache, get_base_dir, get_special_folder, get_temp_dir

__all__ = ["get_special_folder", "get_base_dir", "get_temp_dir", "clear_cache"]
