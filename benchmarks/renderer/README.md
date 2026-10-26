# Renderer spike — 2026-10-26

Measured in Linux PTY 120×32, sequential runs, three repetitions per renderer. Identical functional screen: animated header, persistent machine/session row, last 20 streamed lines, editable input, frame marker. Three seconds idle, three seconds at 60 updates/s, three seconds idle. Real PTY typing is preserved by all nine runs; all exit 0. No AI engine or remote server was contacted. Go was absent and not installed within the timebox.

| Median of three truecolor runs | Rust/Ratatui | Python/Textual | Python/prompt_toolkit |
|---|---:|---:|---:|
| First visible AURORA marker (ms) | 4.122 | 110.474 | 71.607 |
| Render callback p50 (ms) | 0.132 | 1.955 | 4.711 |
| Render callback p95 (ms) | 0.191 | 2.620 | 6.232 |
| Visible update period p50 (ms) | 16.654 | 16.676 | 16.721 |
| Visible update period p95 (ms) | 17.381 | 18.959 | 18.566 |
| Process active CPU (% of one core) | 0.909 | 16.851 | 31.355 |
| Process idle CPU (%) | 0.000 | 0.357 | 0.000 |
| Sampled peak RSS (MiB) | 2.895 | 34.551 | 33.316 |
| PTY bytes for nine seconds | 44,101 | 1,409,122 | 82,089 |
| Visible distinct frame states | 181 | 181 | 181 |

Raw results: `results/metrics.json`, individual `*-frames.json`, terminal captures `*.ansi` and snapshots `*.txt`. The earlier explicit NO_COLOR series is preserved as `results/no-color-metrics.json`; truecolor series removes inherited NO_COLOR from child environments. PNGs are approximate rasterizations of captured terminal cells (not physical Mac terminal screenshots).

## Scope and limitations

- This benchmarks tiny renderer processes, not the complete CLI. A hybrid renderer retains the Python business process; its memory/startup do not disappear.
- Startup is process launch to the first observed marker, includes Python imports, uses warm filesystem caches, and is not readiness of remote tools.
- Callback boundaries differ: Ratatui `Terminal.draw`, prompt_toolkit before/after-render, Textual screen timer layout/compositor refresh. Textual updates outside that callback can be excluded. CPU/RSS/PTY bytes are measured externally and are the stronger cross-framework comparison.
- CPU accounting resolution is 10 ms; short idle windows make a zero a resolution-limited observation, not proof of no CPU under all workloads.
- PTY capture and pyte decoding add observer load outside measured child CPU; no Terminal.app/iTerm2/tmux/SSH or GPU paint latency was measured. 60 Hz refers to emitted state cadence, not guaranteed physical display refresh.
- Native release binary; Python normal interpreter. Small input editing only, no IME, selection, mouse, bidi, Markdown complexity or full CLI command parity was benchmarked.
- Textual Static updates redraw larger regions in this implementation; this is not proof that every optimized Textual application emits this volume.
- No network interruption in this renderer-only spike. Network resilience belongs to CLI transport tests.

## Recommendation

For a 60 Hz terminal renderer with low CPU, use Ratatui as an optional native display process, retaining the existing Python business engine and tests behind versioned JSONL over stdio. Keep Python fallback until native command, keyboard, accessibility, resize and install parity is validated. A wholesale business rewrite is unsupported by this experiment.

## Reproduction

```sh
python3 -m venv --system-site-packages venv
venv/bin/python -m pip install textual==8.2.8 prompt_toolkit==3.0.53 psutil==7.2.2 pyte==0.8.2
CARGO_HOME="$PWD/cargo-cache" cargo build --release --locked --manifest-path rust/Cargo.toml -j 2
venv/bin/python bench.py
```

Observed toolchain: Rust/cargo 1.96.1, Ratatui 0.30.2, Crossterm 0.29.0, Python 3.12, Textual 8.2.8, prompt_toolkit 3.0.53, psutil 7.2.2, pyte 0.8.2. Cargo.lock pins Rust dependencies; existing system/user Python dependencies were visible in this scoped venv. No home/model/configuration files were changed.

Official references consulted: https://ratatui.rs/installation/ and https://textual.textualize.io/widgets/static/ .
