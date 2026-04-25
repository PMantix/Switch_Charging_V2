## Delegation triggers — this repo

Auto-delegate to a subagent for these workflows; they reliably blow
past the 5KB / multi-step thresholds:

- **Firmware deploy + verify**: stop pi service → rsync → BOOTSEL flash
  → restart → tail logs → confirm sensor stream. Whole cycle to one agent.
- **DOE runs**: any `recording_doe` / `plot_cmd_vs_sense` /
  `plot_doe_grid` invocation that produces plots — delegate so the
  plot-output volume stays out of main context.
- **TUI debugging across Textual layers**: render cache + CSS + driver
  issues touch many files; spawn Explore.
- **Build firmware-c/**: ARM toolchain build produces verbose output;
  delegate and ask for a pass/fail summary only.
- **Cross-tier tracing** (TUI ↔ Pi server ↔ RP2040 firmware): always
  Explore — three-tier reads are the canonical >3-file case.

Do NOT delegate for:
- Single Python script edits with a known target
- Reading one known config or memory file
- Quick git ops, status checks, single-file diffs
