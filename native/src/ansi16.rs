//! Crossterm emits indexed SGR even for its named colors. Translate only its
//! 0..15 color parameters when the terminal explicitly has a 16-color budget.
use std::io::{self, Write};

pub struct Ansi16<W: Write> {
    inner: W,
    enabled: bool,
    pending: Vec<u8>,
}
impl<W: Write> Ansi16<W> {
    pub fn new(inner: W, enabled: bool) -> Self {
        Self {
            inner,
            enabled,
            pending: Vec::new(),
        }
    }
    fn emit(&mut self) -> io::Result<()> {
        if self.pending.starts_with(b"\x1b[") && self.pending.ends_with(b"m") {
            let raw = String::from_utf8_lossy(&self.pending[2..self.pending.len() - 1]);
            let values = raw.split(';').collect::<Vec<_>>();
            let mut out = Vec::new();
            let mut i = 0;
            while i < values.len() {
                if matches!(values[i], "38" | "48")
                    && values.get(i + 1) == Some(&"5")
                    && let Some(n) = values
                        .get(i + 2)
                        .and_then(|s| s.parse::<u8>().ok())
                        .filter(|n| *n < 16)
                {
                    let base = if values[i] == "38" { 30 } else { 40 };
                    out.push((base + (n % 8) + if n >= 8 { 60 } else { 0 }).to_string());
                    i += 3;
                } else {
                    out.push(values[i].to_owned());
                    i += 1;
                }
            }
            self.inner
                .write_all(format!("\x1b[{}m", out.join(";")).as_bytes())?;
        } else {
            self.inner.write_all(&self.pending)?;
        }
        self.pending.clear();
        Ok(())
    }
}
impl<W: Write> Write for Ansi16<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if !self.enabled {
            return self.inner.write(bytes);
        }
        for &byte in bytes {
            if self.pending.is_empty() {
                if byte == 0x1b {
                    self.pending.push(byte);
                } else {
                    self.inner.write_all(&[byte])?;
                }
            } else {
                self.pending.push(byte);
                if (self.pending.len() == 2 && byte != b'[')
                    || (self.pending.len() > 2 && (0x40..=0x7e).contains(&byte))
                    || self.pending.len() > 128
                {
                    self.emit()?;
                }
            }
        }
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        // A flush may occur between fragments of a sequence; retain it.
        self.inner.flush()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn translates_fragmented_colors_without_changing_cursor_or_utf8() {
        let mut out = Ansi16::new(Vec::new(), true);
        for byte in b"\x1b[38;5;14;48;5;2mhello\x1b[2;3H\x1b[0m" {
            out.write_all(&[*byte]).unwrap();
            out.flush().unwrap();
        }
        assert_eq!(out.inner, b"\x1b[96;42mhello\x1b[2;3H\x1b[0m");
    }
}
