"""Convenience: run each hand-crafted seed through the runner so it appears in
the dashboard exactly the way an LLM-generated run would.

Usage:
    python examples/import_seeds_to_runs.py [--seed demo|rag|all]
"""
from __future__ import annotations

import argparse
import importlib

from vidforge.runner import VideoRun


SEED_MODULES = {
    "demo": "examples.seed_demo",
    "rag": "examples.seed_rag",
}


def import_seed(name: str) -> None:
    mod = importlib.import_module(SEED_MODULES[name])
    plan = mod.PLAN
    scenes = mod.SCENES
    theme = getattr(mod, "THEME_CSS", "")
    run = VideoRun.from_title(plan.title)
    print(f"\u25b6  importing {name} \u2192 {run.run_dir}")
    manifest = run.execute_seeded(plan, scenes, theme_css=theme)
    print(f"   {'\u2713' if manifest.success else '\u2717'}  {manifest.final_video}")


def main(seed: str) -> None:
    targets = list(SEED_MODULES.keys()) if seed == "all" else [seed]
    for s in targets:
        import_seed(s)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="all", choices=[*SEED_MODULES.keys(), "all"])
    args = parser.parse_args()
    main(args.seed)
