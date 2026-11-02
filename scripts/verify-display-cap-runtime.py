"""Run the display-cap fixture as a clean, isolated synthetic Git baseline."""
import argparse, hashlib, json, os, shutil, subprocess, sys, tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = "apps/web/public/data"
POLICY = ("docs/govintel/source-policy.approved.json", "docs/govintel/retention-rights-policy.v1.json",
          "docs/govintel/source-catalog.v2.json", f"{DATA}/source-policy.json")

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(args, cwd, log=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=1800)
    if log:
        log.write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(f"command failed with exit {p.returncode}; log={log}")
    return p.stdout.strip()

def validate_runtime(receipt, commit):
    if (receipt.get("status") != "PASS" or receipt.get("code_sha") != commit or
            receipt.get("worktree_dirty") is not False or receipt.get("not_run") or
            receipt.get("production_verified") is not False or
            receipt.get("test_mode") != "CURRENT_CHECKOUT_LOOPBACK_HTTP"):
        raise ValueError("display-cap runtime receipt is not a complete isolated build-only PASS")
    checks = {c["id"]: c["status"] for c in receipt.get("checks", [])}
    if checks.get("web_build") != "PASS" or checks.get("build_only_release") != "PASS":
        raise ValueError("display-cap runtime must actually build and bind its release")

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chrome-path", required=True)
    args = parser.parse_args(argv)
    out = args.output.resolve()
    if out.is_relative_to(ROOT) or ROOT.is_relative_to(out) or out.exists():
        raise ValueError("output must be a new directory outside the source checkout")
    if run(["git", "status", "--porcelain"], ROOT):
        raise ValueError("source checkout must remain clean")
    original = {p: digest(ROOT / p) for p in POLICY}
    source_commit = run(["git", "rev-parse", "HEAD"], ROOT)
    out.mkdir(parents=True)
    baseline = out / "baseline"
    baseline.mkdir()
    archive = out / "source.tar"
    with archive.open("wb") as f:
        subprocess.run(["git", "archive", source_commit], cwd=ROOT, stdout=f, check=True, timeout=60)
    with tarfile.open(archive) as f:
        f.extractall(baseline, filter="data")
    baseline_policy = {p: digest(baseline / p) for p in POLICY}
    if any(json.loads((baseline/p).read_text(encoding="utf-8")) !=
           json.loads((ROOT/p).read_text(encoding="utf-8")) for p in POLICY):
        raise ValueError("archived policy differs from source policy")
    run(["git", "init"], baseline)
    run(["git", "add", "."], baseline)
    commit_args = ["git", "-c", "user.name=GovIntel synthetic baseline", "-c",
                   "user.email=synthetic@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m"]
    run(commit_args + ["Preserved snapshot for isolated display-cap test"], baseline)
    # Copy exactly the workflow's preserved fixture, never grant fictional rights.
    feed_path = baseline / DATA / "intelligence-feed.json"
    feed = json.loads(feed_path.read_text(encoding="utf-8"))
    if len(feed["items"]) < 24:
        raise ValueError("display-cap fixture requires at least 24 preserved items")
    for row in feed["items"][:24]:
        row["title"] += "（排程回歸測試）"
    feed_path.write_text(json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8")
    run([sys.executable, "-X", "utf8", "-B", "scripts/build-v2-shadow-brief.py"],
        baseline, out / "build-fixture.log")
    brief = json.loads((baseline / DATA / "v2-daily-brief.json").read_text(encoding="utf-8"))
    displayed = sum(len(brief[k]) for k in ("priority_items", "tracking_items", "other_changes"))
    if brief["overview"]["current_change_count"] != 24 or displayed != 23:
        raise ValueError("display-cap fixture did not reproduce 24 changes / 23 displayed")
    for p in POLICY:
        if digest(baseline / p) != baseline_policy[p]:
            raise ValueError("fixture modified real rights/governance inputs")
    dirty_paths = run(["git", "diff", "--name-only"], baseline).splitlines()
    required_dirty = {f"{DATA}/intelligence-feed.json", f"{DATA}/v2-daily-brief.json"}
    allowed_dirty = required_dirty | {"state/v2-shadow-state.json", "state/v2-handoff-state.json"}
    if not required_dirty.issubset(dirty_paths) or set(dirty_paths) - allowed_dirty:
        raise ValueError("isolated dirty fixture does not match the display-cap scenario")
    run(["git", "add", "."], baseline)
    run(commit_args + ["Synthetic display-cap runtime baseline"], baseline)
    commit = run(["git", "rev-parse", "HEAD"], baseline)
    fixture_hashes = {n: digest(baseline / DATA / n) for n in
                      ("intelligence-feed.json", "source-status.json", "v2-daily-brief.json")}
    env = dict(os.environ)
    env.pop("GOVINTEL_SKIP_DIRTY_FIXTURE_RUNTIME", None)
    env.pop("GOVINTEL_PUBLICATION_WORKFLOW", None)
    # Use the original verifier unchanged: full build, HTTP, browsers and sabotage.
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", "scripts/verify-current-checkout.py",
                        "--mode", "full", "--chrome-path", args.chrome_path,
                        "--output", str(out / "runtime")], cwd=baseline, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=1800)
    (out / "runtime.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
    receipt = json.loads((out / "runtime/receipt.json").read_text(encoding="utf-8"))
    validate_runtime(receipt, commit)
    if p.returncode:
        raise ValueError("original runtime verifier returned failure")
    if run(["git", "status", "--porcelain"], ROOT) or any(digest(ROOT/p) != original[p] for p in POLICY):
        raise ValueError("source checkout or governance inputs changed")
    result = {"status": "PASS", "execution_scope": "ISOLATED_SYNTHETIC_DISPLAY_CAP_RUNTIME",
              "source_commit": source_commit, "synthetic_baseline_commit": commit,
              "source_archive_sha256": digest(archive),
              "changes": 24, "displayed": 23, "fixture_hashes": fixture_hashes,
              "dirty_fixture_paths_before_baseline_commit": dirty_paths,
              "policy_hashes_unchanged": original, "runtime_receipt": "runtime/receipt.json",
              "baseline_policy_hashes_unchanged": baseline_policy,
              "production_verified": False, "actual_purge": "NOT_RUN"}
    (out / "receipt.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print("DISPLAY_CAP_RUNTIME_OK " + json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

