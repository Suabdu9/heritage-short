"""Command line entry point.

    python -m pipeline film          validate -> narration -> keyframes -> shots -> music -> assemble
    python -m pipeline radio         validate -> narration -> music -> radio cut (black screen)
    python -m pipeline <stage>       run one stage: validate | script | smoke | narration | keyframes |
                                     shots | music | assemble | costs
    python -m pipeline review        print every narration line + subtitle for checking
    python -m pipeline voices [ids]  voice the first line with several Addis voices (or the ids given)
    python -m pipeline explore s02 4 four seed variations of one keyframe (no cache change)
    python -m pipeline kaggle-push | kaggle-status | kaggle-pull

Options:
    --only s03,s07       limit NEW model calls to these shots (others use the cache)
    --storyboard PATH    use another storyboard file
"""
from __future__ import annotations

import argparse
import sys
import time

from .common import (RUN_ID, ConsentError, MissingKey, StoryboardError, load_env, load_storyboard, log_event, say)


def main(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--only", default="")
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--text", help="text for `voices` (default: the first narration line)")
    ap.add_argument("--tts", choices=["addis", "gemini"], help="force a narration provider (for comparisons)")
    a = ap.parse_args(argv)
    only = set(x.strip() for x in a.only.split(",") if x.strip()) or None

    from . import assemble, costs, kaggle_jobs, keyframes, music, narration, script, shots, smoke

    if a.command in ("kaggle-push", "kaggle-status", "kaggle-pull"):
        {"kaggle-push": kaggle_jobs.push, "kaggle-status": kaggle_jobs.status, "kaggle-pull": kaggle_jobs.pull}[a.command]()
        return 0
    if a.command == "costs":
        costs.run()
        return 0

    try:
        sb = load_storyboard(a.storyboard)
    except StoryboardError as e:
        say(str(e))
        return 2
    say(f"storyboard OK: '{sb['title']}', {len(sb['shots'])} shots, language={sb['language']}  (run {RUN_ID})")
    log_event("validate", status="ok", shots=len(sb["shots"]))

    def stage(name, fn, *args, **kw):
        say(f"\n== {name} ==")
        t0 = time.time()
        res = fn(*args, **kw)
        say(f"   {name}: {res if not isinstance(res, list) else 'done'}  ({time.time() - t0:.1f}s)")
        return res

    try:
        c = a.command
        if c == "validate":
            return 0
        if c == "smoke":
            return 0 if smoke.run(sb) else 1
        if c == "review":
            narration.review(sb)
            return 0
        if c == "voices":
            narration.audition(sb, a.args or None, a.text)
            return 0
        if c == "script":
            stage("script check", script.run, sb)
        elif c == "narration":
            stage("narration", narration.run, sb, only, a.tts)
        elif c == "keyframes":
            stage("keyframes", keyframes.run, sb, only)
        elif c == "explore":
            if not a.args:
                say("usage: python -m pipeline explore <shot_id> [n]")
                return 2
            stage("explore", keyframes.explore, sb, a.args[0], int(a.args[1]) if len(a.args) > 1 else 4)
        elif c == "shots":
            stage("shots", shots.run, sb)
        elif c == "music":
            stage("music", music.run, sb)
        elif c == "assemble":
            stage("assemble", assemble.run, sb)
        elif c == "radio":
            stage("narration", narration.run, sb, only, a.tts)
            stage("music", music.run, sb)
            stage("radio cut", assemble.run, sb, radio=True)
        elif c == "film":
            stage("narration", narration.run, sb, only, a.tts)
            stage("keyframes", keyframes.run, sb, only)
            stage("shots", shots.run, sb)
            stage("music", music.run, sb)
            stage("assemble", assemble.run, sb)
            say("\nDone. Queued GPU work (if any) is in outputs/clips/job.json and outputs/music/job.json;"
                " run `python -m pipeline kaggle-push`, then `kaggle-pull`, then `film` again.")
        else:
            say(f"unknown command '{c}'\n{__doc__}")
            return 2
    except ConsentError as e:
        say(f"\nSTOPPED (consent gate): {e}")
        return 3
    except narration.NarrationNotApproved as e:
        say(f"\nSTOPPED: {e}")
        return 5
    except MissingKey as e:
        say(f"\nSTOPPED: {e}")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
