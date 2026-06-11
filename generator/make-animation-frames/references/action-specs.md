# Action Specs

Use repeated `--action` flags when preparing a run.

Accepted formats:

```text
name=frames:description
name:frames:description
name=description
name
```

Examples:

```text
idle
wave
jump
cheer
think
work
focus
move-right
move-left
cast=8:small spell cast with hands close to body
hurt=4:brief recoil and recover
```

Built-in action ids have default frame counts and guidance in `scripts/prepare_animation_run.py`.
Use lowercase hyphenated action ids in filenames. The prepare script normalizes user-facing action names automatically.
