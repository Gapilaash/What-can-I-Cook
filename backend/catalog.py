"""Recipe catalog shared with the admin dashboard.

The dashboard writes recipes to the `admin_recipes` table in the SAME database.
This module reads them (published ones only) so dashboard changes show up on the
website. If the table doesn't exist yet (dashboard never ran) it falls back to the
built-in recipes_data.py. Results are cached for a few seconds to keep things fast.
"""
import json
import time

from database import get_conn
from recipes_data import ALL_INGREDIENTS as _STATIC_INGREDIENTS, RECIPES as _STATIC_RECIPES

TTL = 15  # seconds
_cache = {"t": 0.0, "recipes": None, "by_id": None, "ingredients": None, "categories": None}


def _load():
    try:
        with get_conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM admin_recipes WHERE published = 1 ORDER BY name").fetchall()]
            try:
                cats = [dict(r) for r in c.execute("SELECT slug, name, emoji FROM categories ORDER BY name").fetchall()]
            except Exception:
                cats = []
    except Exception as e:  # table missing / DB not reachable -> built-in recipes
        print("[catalog] using built-in recipes:", str(e)[:120])
        return [dict(r) for r in _STATIC_RECIPES], list(_STATIC_INGREDIENTS), []

    recipes = []
    for r in rows:
        try:
            ing = json.loads(r.get("ingredients") or "[]")
            tags = json.loads(r.get("tags") or "[]")
            steps = json.loads(r.get("instructions") or "[]")
        except Exception:
            continue
        if not ing:
            continue
        cat = r.get("category") or ""
        if cat and cat not in tags:
            tags = [cat] + tags
        item = {"id": str(r["id"]), "name": r["name"], "cook_time": r.get("cook_time"),
                "servings": r.get("servings"), "tags": tags, "ingredients": ing,
                "instructions": steps, "category": cat}
        if r.get("image"):
            item["image"] = r["image"]
        recipes.append(item)
    ingredients = sorted({i for r in recipes for i in r["ingredients"]} | set(_STATIC_INGREDIENTS))
    return recipes, ingredients, cats


def _fresh():
    if _cache["recipes"] is None or time.time() - _cache["t"] > TTL:
        recipes, ingredients, cats = _load()
        _cache.update(t=time.time(), recipes=recipes, by_id={str(r["id"]): r for r in recipes},
                      ingredients=ingredients, categories=cats)
    return _cache


def recipes() -> list:
    return _fresh()["recipes"]


def by_id() -> dict:
    return _fresh()["by_id"]


def all_ingredients() -> list:
    return _fresh()["ingredients"]


def categories() -> list:
    return _fresh()["categories"]
