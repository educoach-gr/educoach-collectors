"""Runs the collectors: python3 -m collectors.run [--dry-run] [--only fek,diavgeia,browser] [--explain]

Each collector is isolated: when one fails the rest still run, and the failure is reported to the gateway
(inbox_add with `error`), so the research run can tell the owner that a source is down.

  --dry-run            nothing is sent; items are printed as JSON on stdout (logs go to stderr)
  --state-file <path>  with --dry-run: read and write the cursors in this file (to test «new since last run»)
  --only a,b           run only these collectors
  --sources x,y        browser collector: only these sources of browser_sources.json
  --explain            log why each candidate was kept or dropped
"""
import importlib, sys, time, traceback

from .common import Run, log, report_error

COLLECTORS = ['fek', 'diavgeia', 'browser']


def main(argv):
    def opt(flag):
        if flag in argv:
            i = argv.index(flag)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return None
    only = opt('--only')
    state_file = opt('--state-file')
    sources = opt('--sources')
    dry = '--dry-run' in argv
    explain = '--explain' in argv
    names = [n.strip() for n in only.split(',')] if only else COLLECTORS
    bad = [n for n in names if n not in COLLECTORS]
    if bad:
        raise SystemExit('unknown collector: ' + ', '.join(bad) + ' (have: ' + ', '.join(COLLECTORS) + ')')
    failed = 0
    summary = []
    for name in names:
        t0 = time.time()
        log(f'== {name}')
        try:
            run = Run(dry=dry, state_file=state_file)
            kw = {'only': [x.strip() for x in sources.split(',')]} if sources and name == 'browser' else {}
            importlib.import_module('.' + name, __package__).collect(run, explain=explain, **kw)
            summary.append(f'{name}: {run.sent} items' + ('' if dry else f' sent, {run.added} new, {run.duplicates} already known')
                           + f' ({time.time() - t0:.0f}s)')
        except Exception as e:
            failed += 1
            log(traceback.format_exc())
            report_error(name, f'{type(e).__name__}: {e}', dry=dry)
            summary.append(f'{name}: FAILED: {type(e).__name__}: {str(e)[:200]}')
    log('')
    for s in summary:
        log(s)
    return 1 if failed == len(names) else 0   # the job fails only when nothing worked


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
