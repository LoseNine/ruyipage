# Action Visual Preload Separation

## Problem

`action_visual` currently controls whether ruyiPage registers a BiDi preload
script. Fingerprint Pro reports `Developer Tools: Yes` when no preload is
active and `Developer Tools: Not detected` when any preload, including an
empty one, is active. This couples an unrelated visualization setting to the
browser's detection result.

The XPath used for live verification is:

```text
/html/body/div[2]/div/div[3]/div/div[2]/div/table/tbody/tr[6]/td[2]
```

It represents the `Developer Tools` smart signal. The separate `Bot` signal
already reports `Not detected` in both configurations.

## Required Behavior

| `action_visual` | Baseline preload | Visual API and DOM | Developer Tools signal |
| --- | --- | --- | --- |
| `False` | Active | Absent | `Not detected` |
| `True` | Active | Present | `Not detected` |

The parameter continues to control visualization only. It no longer controls
whether the browser has an active BiDi preload.

## Considered Approaches

### 1. Separate session baseline and visual behavior in ruyiPage (selected)

Always register one harmless preload owned by the current browser session.
Keep the existing visualization preload separate and register it only when
`action_visual` is enabled. Disabled mode does not create `window.__ruyiAV` or
any `__ruyi_av_*` DOM nodes.

This is the smallest change that matches the observed causal boundary and
preserves current visualization behavior across navigation.

### 2. Patch the custom Firefox runtime

Change the browser kernel or remote-debugging implementation so the signal is
stable without a preload. This could be more durable, but that runtime is
outside this repository and would substantially expand the work.

### 3. Add a separate stealth option

Expose the baseline preload as a new user setting. This makes the old coupling
explicit but conflicts with the requirement that `action_visual` control only
visual output and that the default configuration retain the expected signal.

## Design

The `Firefox` browser object will own a dedicated baseline preload lifecycle:

- A baseline script ID and its BiDi session ID are stored independently from
  `_action_visual_global_preload_script_id`.
- A browser-level lock protects the session check and registration operation.
- Successful session creation invokes one common activation hook that ensures
  `() => {}` is registered for that session.
- Context initialization asks the same browser-owned helper to ensure the
  baseline, providing a retry point while the lock and session token keep
  successful registrations idempotent.
- Reconnect creates a new session ID, so the hook registers a fresh baseline
  instead of trusting the stale ID from the previous session.
- Browser objects created from a retained probe session run the same baseline
  setup before they are returned.

`FirefoxBase._maybe_enable_action_visual()` keeps its current option guard:

- With visualization disabled, it returns without registering or evaluating
  the visual script.
- With visualization enabled, it registers the existing action visual preload
  and evaluates it in the current document, preserving navigation behavior.
- Its script ID remains exclusively associated with the visual preload.

Navigation reinjection remains guarded by `action_visual_enabled`, so the
disabled path never adds visual globals or DOM nodes.

`action_visual` is explicitly a browser-startup option. Runtime mutation of
the retained `FirefoxOptions` object is not a supported enable/disable API;
this matches the existing construction and documentation pattern. No public
API or configuration schema changes are required.

## Error Handling

Baseline registration and visual injection use separate error boundaries.
Baseline setup retries once when BiDi raises or returns an empty script ID. If
both attempts fail, the browser stores no success token and emits a warning so
the degraded invariant is observable and a later session/context setup can
retry. A baseline failure does not suppress enabled-mode current-document
visualization.

Visual preload and current-document injection retain their existing
best-effort debug logging. Disabled visualization does not execute the visual
script regardless of baseline outcome.

## Testing

Automated regression tests will prove that:

1. Session activation registers exactly one empty baseline preload.
2. Repeated and concurrent setup for one session does not duplicate it.
3. A changed session ID registers a new baseline after reconnect.
4. Empty IDs and BiDi failures are retried, warning-logged, and not recorded as
   success.
5. Disabled visualization does not register or evaluate the visual script.
6. Enabled visualization registers and evaluates the existing visual script.
7. Existing action visual and protocol-conformance tests remain green.

A live A/B verification will launch fresh browser profiles with
`action_visual=False` and `action_visual=True`, then assert:

- the target XPath reports `Not detected` in both runs;
- the containing row label is still `Developer Tools` before its value is
  interpreted;
- the separate `Bot` row reports `Not detected` in both runs;
- `window.__ruyiAV` and `__ruyi_av_*` nodes are absent when disabled;
- `window.__ruyiAV` and the visual nodes are present when enabled.

A no-baseline negative control will also be run against the previously
released revision to confirm that the same environment still produces
`Developer Tools: Yes` without an active preload.

The live third-party check is verification evidence rather than a committed
test because its server-side model and network availability are external.

## Scope

This change is limited to browser-session baseline preload registration,
action visual lifecycle separation, the startup-option contract, and focused
tests. It does not modify fingerprint profile generation, browser binaries,
XPath picker behavior, or unrelated user changes already present in the work
tree.
