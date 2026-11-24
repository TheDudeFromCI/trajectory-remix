use crate::{invalid, slots::Slots, Emission, Result, Token};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub(crate) const NONE: u32 = u32::MAX;
pub(crate) const FOREVER: u64 = u64::MAX;
pub(crate) const DURABLE: u8 = 1;
pub(crate) const MODEL: u8 = 2;
pub(crate) const NOOP: u8 = 4;
pub(crate) const EOS: u8 = 8;
pub(crate) const BOS: u8 = 16;
pub(crate) const READ_ONLY: u8 = 1;
pub(crate) const INITIALLY_AVAILABLE: u8 = 2;

#[derive(Debug, Default, Serialize)]
pub struct Columns {
    pub token_ids: Vec<u32>,
    pub window_ids: Vec<u32>,
    pub position_ids: Vec<u32>,
    pub query_frontiers: Vec<u64>,
    pub visible_from: Vec<u64>,
    pub visible_until: Vec<u64>,
    pub previous_rows: Vec<u32>,
    pub loss_weights: Vec<f64>,
    pub row_flags: Vec<u8>,
    pub frame_offsets: Vec<u64>,
    pub window_slots: Vec<u32>,
    pub window_open_frontiers: Vec<u64>,
    pub window_close_frontiers: Vec<u64>,
    pub window_activation_frontiers: Vec<u64>,
    pub window_flags: Vec<u8>,
    pub target_rows: Vec<u32>,
}

struct Window {
    logical: Option<String>,
    slot: u32,
    writable: bool,
    opened: u64,
    active: u64,
    closed: u64,
    initially_available: bool,
    rows: Vec<u32>,
    header_last: Option<u32>,
    body_start: usize,
}

pub(crate) struct Runtime {
    pub columns: Columns,
    pub live: BTreeMap<String, usize>,
    pub peak: usize,
    active: usize,
    windows: Vec<Window>,
    used: BTreeSet<u32>,
    frontier: u64,
    rng: Slots,
    available: usize,
    limit: usize,
    max_rows: usize,
    bos: u32,
    references: Vec<(usize, String, usize)>,
}

impl Runtime {
    pub fn new(limit: u32, max_rows: usize, bos: u32, seed: &str) -> Result<Self> {
        let mut state = Self {
            columns: Columns {
                frame_offsets: vec![0],
                ..Columns::default()
            },
            live: BTreeMap::new(),
            windows: Vec::new(),
            used: BTreeSet::new(),
            frontier: 0,
            peak: 0,
            active: 0,
            rng: Slots::new(seed),
            available: 0,
            limit: limit as usize,
            max_rows,
            bos,
            references: Vec::new(),
        };
        state.new_window(None, true)?;
        Ok(state)
    }

    fn new_window(&mut self, logical: Option<&str>, writable: bool) -> Result<usize> {
        let free = self.limit + 1 - self.used.len();
        if free == 0 {
            return Err(invalid(
                "Active window capacity exceeded (Available is excluded)",
            ));
        }
        let mut slot = self.rng.choose(free as u32) as u32;
        // Select the same rank among the free slots without allocating one
        // element for each unused physical slot.
        for &occupied in &self.used {
            if occupied > slot {
                break;
            }
            slot += 1;
        }
        self.used.insert(slot);
        let index = self.windows.len();
        self.windows.push(Window {
            logical: logical.map(str::to_owned),
            slot,
            writable,
            opened: self.frontier,
            active: FOREVER,
            closed: FOREVER,
            initially_available: logical.is_none(),
            rows: Vec::new(),
            header_last: None,
            body_start: 0,
        });
        self.insert(
            index,
            &Token::control(self.bos, "bos", true),
            false,
            0.0,
            None,
            None,
        )?;
        Ok(index)
    }

    pub fn open(&mut self, logical: &str, model: bool, writable: bool) -> Result<u32> {
        if let Some(&index) = self.live.get(logical) {
            return Ok(self.windows[index].slot);
        }
        if self.active >= self.limit {
            return Err(invalid(
                "Active window capacity exceeded (Available is excluded)",
            ));
        }
        let index = if model {
            self.windows[self.available].logical = Some(logical.to_owned());
            self.available
        } else {
            self.new_window(Some(logical), writable)?
        };
        self.live.insert(logical.to_owned(), index);
        self.peak = self.peak.max(self.active + 1);
        Ok(self.windows[index].slot)
    }

    pub fn writable(&self, index: usize) -> bool {
        self.windows[index].writable
    }

    fn insert(
        &mut self,
        index: usize,
        token: &Token,
        model: bool,
        weight: f64,
        commit: Option<u64>,
        previous: Option<u32>,
    ) -> Result<()> {
        let a = &mut self.columns;
        let w = &mut self.windows[index];
        let row = a.token_ids.len();
        if row >= self.max_rows {
            return Err(invalid("Compilation row bound exceeded"));
        }
        let previous = previous.unwrap_or_else(|| {
            w.rows
                .iter()
                .rev()
                .copied()
                .find(|&row| {
                    let row = row as usize;
                    a.visible_from[row] <= self.frontier && self.frontier < a.visible_until[row]
                })
                .unwrap_or(NONE)
        });
        let durable = !matches!(token.control, Some("noop" | "eos"));
        let mut flags = if durable { DURABLE } else { 0 } | if model { MODEL } else { 0 };
        flags |= match token.control {
            Some("bos") => BOS,
            Some("eos") => EOS,
            Some("noop") => NOOP,
            _ => 0,
        };
        let boundary = commit.unwrap_or(self.frontier + 1);
        a.token_ids.push(token.id.unwrap_or_default());
        a.window_ids.push(index as u32);
        a.position_ids.push(if previous == NONE {
            0
        } else {
            a.position_ids[previous as usize] + 1
        });
        a.query_frontiers.push(self.frontier);
        a.visible_from.push(if durable { boundary } else { 0 });
        a.visible_until.push(if durable { FOREVER } else { 0 });
        a.previous_rows.push(previous);
        a.loss_weights.push(weight);
        a.row_flags.push(flags);
        if let Some((logical, digit)) = &token.reference {
            self.references.push((row, logical.clone(), *digit));
        }
        if weight > 0.0 {
            a.target_rows.push(row as u32);
        }
        if durable {
            w.rows.push(row as u32);
            if token.header {
                w.header_last = Some(row as u32);
                w.body_start = w.rows.len();
            }
        }
        if commit.is_none() {
            self.frontier = boundary;
        }
        Ok(())
    }

    fn activate(&mut self, index: usize, boundary: u64) {
        let window = &mut self.windows[index];
        if window.active == FOREVER {
            window.active = boundary;
            if window.closed == FOREVER {
                self.active += 1;
            }
        }
    }

    fn close(&mut self, index: usize, boundary: u64) {
        let window = &mut self.windows[index];
        window.closed = boundary;
        if window.active != FOREVER {
            self.active -= 1;
        }
        for &row in &window.rows {
            let until = &mut self.columns.visible_until[row as usize];
            *until = boundary.min(*until);
        }
        self.used.remove(&window.slot);
        if let Some(logical) = &window.logical {
            self.live.remove(logical);
        }
    }

    pub fn input(&mut self, emission: &Emission, writable: bool) -> Result<()> {
        self.open(&emission.window, false, writable)?;
        let index = self.live[&emission.window];
        let window = &mut self.windows[index];
        let previous = if emission.replace {
            for &row in &window.rows[window.body_start..] {
                let until = &mut self.columns.visible_until[row as usize];
                *until = (self.frontier + 1).min(*until);
            }
            window.rows.truncate(window.body_start);
            window.header_last
        } else {
            None
        };
        for (offset, token) in emission.tokens.iter().enumerate() {
            self.insert(
                index,
                token,
                false,
                emission.weight,
                None,
                if offset == 0 { previous } else { None },
            )?;
            if token.control == Some("eos") {
                self.close(index, self.frontier);
            }
        }
        self.activate(index, self.frontier);
        Ok(())
    }

    pub fn output(&mut self, emissions: &[Emission]) -> Result<()> {
        let boundary = self.frontier + 1;
        let mut replace_available = false;
        for emission in emissions {
            let index = if emission.window == "@available" && emission.event.is_none() {
                self.available
            } else {
                self.live[&emission.window]
            };
            for token in &emission.tokens {
                self.insert(index, token, true, emission.weight, Some(boundary), None)?;
                if !matches!(token.control, Some("eos" | "noop"))
                    && self.windows[index].active == FOREVER
                {
                    self.activate(index, boundary);
                    replace_available |= index == self.available;
                }
            }
        }
        for emission in emissions {
            if emission
                .tokens
                .iter()
                .any(|token| token.control == Some("eos"))
            {
                self.close(self.live[&emission.window], boundary);
            }
        }
        self.frontier = boundary;
        if replace_available {
            self.available = self.new_window(None, true)?;
        }
        self.peak = self.peak.max(self.active);
        self.columns
            .frame_offsets
            .push(self.columns.token_ids.len() as u64);
        Ok(())
    }

    pub fn resolve_references(&mut self, alphabet: &[u32; 16]) -> Result<()> {
        let slots: BTreeMap<_, _> = self
            .windows
            .iter()
            .filter_map(|window| Some((window.logical.as_deref()?, window.slot)))
            .collect();
        for (row, logical, digit) in &self.references {
            let slot = slots
                .get(logical.as_str())
                .ok_or_else(|| invalid(format!("Referenced window never opens: {logical}")))?;
            self.columns.token_ids[*row] = alphabet[((slot >> (4 * (7 - digit))) & 15) as usize];
        }
        Ok(())
    }

    pub fn has_references(&self) -> bool {
        !self.references.is_empty()
    }

    pub fn finish(mut self) -> (Columns, Vec<Option<String>>) {
        let a = &mut self.columns;
        let mut names = Vec::with_capacity(self.windows.len());
        for window in self.windows {
            names.push(window.logical);
            a.window_slots.push(window.slot);
            a.window_open_frontiers.push(window.opened);
            a.window_close_frontiers.push(window.closed);
            a.window_activation_frontiers.push(window.active);
            a.window_flags.push(
                if window.writable { 0 } else { READ_ONLY }
                    | if window.initially_available {
                        INITIALLY_AVAILABLE
                    } else {
                        0
                    },
            );
        }
        (self.columns, names)
    }
}
