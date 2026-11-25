// Preserve the seeded slot allocation of the original compiler. Python Random
// seeds MT19937 with the UTF-8 seed followed by its SHA-512 digest.
use sha2::{Digest, Sha512};

/// Mersenne Twister seeded the way Python's `random.Random(str)` is, so physical
/// slot choices match the original Python compiler exactly.
pub(crate) struct Slots {
    state: [u32; 624],
    index: usize,
}

impl Slots {
    pub fn new(seed: &str) -> Self {
        let mut bytes = seed.as_bytes().to_vec();
        bytes.extend_from_slice(&Sha512::digest(seed.as_bytes()));
        bytes.reverse();
        let key: Vec<_> = bytes
            .chunks(4)
            .map(|word| {
                let mut bytes = [0; 4];
                bytes[..word.len()].copy_from_slice(word);
                u32::from_le_bytes(bytes)
            })
            .collect();
        let mut state = [0u32; 624];
        state[0] = 19650218;
        for i in 1..624 {
            state[i] = (state[i - 1] ^ (state[i - 1] >> 30))
                .wrapping_mul(1812433253)
                .wrapping_add(i as u32);
        }
        let (mut i, mut j) = (1, 0);
        for _ in 0..624.max(key.len()) {
            state[i] = (state[i] ^ (state[i - 1] ^ (state[i - 1] >> 30)).wrapping_mul(1664525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            if i == 624 {
                state[0] = state[623];
                i = 1;
            }
            j = (j + 1) % key.len();
        }
        for _ in 0..623 {
            state[i] = (state[i] ^ (state[i - 1] ^ (state[i - 1] >> 30)).wrapping_mul(1566083941))
                .wrapping_sub(i as u32);
            i += 1;
            if i == 624 {
                state[0] = state[623];
                i = 1;
            }
        }
        state[0] = 1 << 31;
        Self { state, index: 624 }
    }

    fn next(&mut self) -> u32 {
        if self.index == 624 {
            for i in 0..624 {
                let word = (self.state[i] & 0x80000000) | (self.state[(i + 1) % 624] & 0x7fffffff);
                self.state[i] = self.state[(i + 397) % 624]
                    ^ (word >> 1)
                    ^ if word & 1 == 1 { 0x9908b0df } else { 0 };
            }
            self.index = 0;
        }
        let mut word = self.state[self.index];
        self.index += 1;
        word ^= word >> 11;
        word ^= (word << 7) & 0x9d2c5680;
        word ^= (word << 15) & 0xefc60000;
        word ^ (word >> 18)
    }

    /// Returns a uniform index below `len` using rejection sampling (like
    /// `Random._randbelow`).
    pub fn choose(&mut self, len: u32) -> usize {
        let shift = len.leading_zeros();
        loop {
            let value = self.next() >> shift;
            if value < len {
                return value as usize;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn physical_slot_randomness_matches_python() {
        let mut rng = Slots::new("KTREMIX");
        let chosen: Vec<_> = [33, 32, 16, 2, 1, 13, 1024, 1907]
            .into_iter()
            .map(|length| rng.choose(length))
            .collect();
        assert_eq!(chosen, [26, 16, 7, 1, 0, 3, 682, 1733]);
    }
}
