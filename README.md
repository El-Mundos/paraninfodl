# paraninfodl

Download ebooks from [ebooks.paraninfo.es](https://ebooks.paraninfo.es) as PDF. Chromium is installed automatically on first run.

## Installation

### pipx (recommended)

```bash
pipx install git+https://github.com/El-Mundos/paraninfodl
```

### uv

```bash
uv tool install git+https://github.com/El-Mundos/paraninfodl
```

### pip

```bash
pip install git+https://github.com/El-Mundos/paraninfodl
```

### Arch Linux (AUR)

```bash
git clone https://aur.archlinux.org/paraninfodl.git
cd paraninfodl
makepkg -si
```

Or with an AUR helper:

```bash
yay -S paraninfodl
# or
paru -S paraninfodl
```

### From source

```bash
git clone https://github.com/El-Mundos/paraninfodl
cd paraninfodl
pip install .
```

## Usage

```bash
paraninfodl <url>
```

The first run opens a browser window for Google login. The session is saved to `~/.paraninfo_session.json` and reused automatically from then on.

## Options

| Flag | Description |
|------|-------------|
| `--quality N` | Re-encode images at JPEG quality N (1–95). Lower = smaller file. Omit for lossless. |
| `--text-layer` | Add a selectable/searchable text layer to the PDF. |
| `--keep-pages` | Keep the downloaded page images after building the PDF. |

## Examples

```bash
# Lossless PDF
paraninfodl https://ebooks.paraninfo.es/reader/my-book

# Compressed + searchable text
paraninfodl https://ebooks.paraninfo.es/reader/my-book --quality 75 --text-layer

# Keep page images for inspection
paraninfodl https://ebooks.paraninfo.es/reader/my-book --keep-pages
```

## Notes

- Only works with books your account has access to.
- The saved session contains your cookies — don't share it.
- If a download is interrupted, re-running the same command resumes from where it left off.
