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

### 1. Separate baseline and visual behavior in ruyiPage (selected)

Always register one harmless preload for the browser session. When
`action_visual` is enabled, use the existing visualization preload and inject
the current document. When it is disabled, register an empty function and do
not create `window.__ruyiAV` or any `__ruyi_av_*` DOM nodes.

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

`FirefoxBase._maybe_enable_action_visual()` will always ensure that the browser
has one global preload script ID:

- With visualization disabled, it registers `() => {}` and returns without
  evaluating the visualization script in the current document.
- With visualization enabled, it registers the existing action visual script
  and evaluates it in the current document, preserving current behavior.
- The browser-level script ID continues to prevent duplicate registration when
  several tabs or contexts share the same browser.

Navigation reinjection remains guarded by `action_visual_enabled`, so the
disabled path never adds visual globals or DOM nodes.

No public API or configuration schema changes are required.

## Error Handling

Preload registration keeps the existing best-effort behavior: BiDi failures
are logged at debug level and do not prevent page construction. Disabled
visualization must not execute the visual script even if registration fails.

## Testing

Automated regression tests will prove that:

1. Disabled visualization registers exactly one empty global preload.
2. Disabled visualization does not evaluate the visual script in the current
   document.
3. Enabled visualization registers and evaluates the existing visual script.
4. Repeated initialization across contexts does not duplicate the preload.
5. Existing action visual tests remain green.

A live A/B verification will launch fresh browser profiles with
`action_visual=False` and `action_visual=True`, then assert:

- the target XPath reports `Not detected` in both runs;
- `window.__ruyiAV` and `__ruyi_av_*` nodes are absent when disabled;
- `window.__ruyiAV` and the visual nodes are present when enabled.

The live third-party check is verification evidence rather than a committed
test because its server-side model and network availability are external.

## Scope

This change is limited to action visual preload registration and focused
tests. It does not modify fingerprint profile generation, browser binaries,
XPath picker behavior, or unrelated user changes already present in the work
tree.
