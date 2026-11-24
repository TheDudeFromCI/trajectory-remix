//! Bounded, batched and optionally non-repeating sampling across shards.

use crate::shard::{compatibility, Block, Sample, Shard};
use crate::{invalid, Result};
use rand::distributions::{Distribution, WeightedIndex};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::File;
use std::path::Path;
use uuid::Uuid;

#[derive(Clone, Debug, serde::Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SamplingOptions {
    pub batch_size: usize,
    /// Absolute shard probabilities. By default every record has equal probability.
    pub shard_weights: Option<Vec<f64>>,
    /// Visit each positively weighted sample UUID at most once per epoch.
    pub without_replacement: bool,
    pub seed: u64,
    pub max_open_shards: usize,
    pub decode_workers: usize,
}

impl Default for SamplingOptions {
    fn default() -> Self {
        Self {
            batch_size: 1,
            shard_weights: None,
            without_replacement: false,
            seed: 0,
            max_open_shards: 32,
            decode_workers: 1,
        }
    }
}

struct Selection {
    remaining: usize,
    swaps: HashMap<usize, usize>,
}

impl Selection {
    fn remove(&mut self, at: usize) -> usize {
        self.remaining -= 1;
        let chosen = self.swaps.remove(&at).unwrap_or(at);
        if at != self.remaining {
            let last = self.swaps.remove(&self.remaining).unwrap_or(self.remaining);
            self.swaps.insert(at, last);
        }
        chosen
    }
}

pub struct Dataset {
    shards: Vec<Shard>,
    options: SamplingOptions,
    selections: Vec<Selection>,
    seen: HashSet<Uuid>,
    rng: StdRng,
    files: VecDeque<(usize, File)>,
    workers: Option<rayon::ThreadPool>,
    failed: bool,
}

impl Dataset {
    pub fn open<P: AsRef<Path>>(
        paths: impl IntoIterator<Item = P>,
        options: SamplingOptions,
    ) -> Result<Self> {
        if options.batch_size == 0 || options.max_open_shards == 0 || options.decode_workers == 0 {
            return Err(invalid(
                "batch_size, max_open_shards and decode_workers must be positive",
            ));
        }
        let mut shards = Vec::new();
        let mut seen = HashSet::new();
        let mut identity = None;
        for path in paths {
            let shard = Shard::open(path)?;
            let next = compatibility(&shard.manifest)?;
            if !seen.insert(shard.shard_id) {
                return Err(invalid("Duplicate KTREMIX shard ID"));
            }
            if identity.as_ref().is_some_and(|previous| previous != &next) {
                return Err(invalid(
                    "Incompatible tokenizer/control IDs/execution contract",
                ));
            }
            identity = Some(next);
            shards.push(shard);
        }
        if let Some(weights) = &options.shard_weights {
            if weights.len() != shards.len()
                || weights.iter().any(|w| !w.is_finite() || *w < 0.0)
                || !weights.iter().sum::<f64>().is_finite()
            {
                return Err(invalid("Provide one finite nonnegative weight per shard"));
            }
        }
        let workers = if options.decode_workers > 1 {
            Some(
                rayon::ThreadPoolBuilder::new()
                    .num_threads(options.decode_workers)
                    .build()
                    .map_err(|error| invalid(error.to_string()))?,
            )
        } else {
            None
        };
        let mut dataset = Self {
            shards,
            options,
            selections: Vec::new(),
            seen: HashSet::new(),
            rng: StdRng::seed_from_u64(0),
            files: VecDeque::new(),
            workers,
            failed: false,
        };
        dataset.reset_epoch(0);
        if !dataset.weights().any(|weight| weight > 0.0) {
            return Err(invalid("Dataset has no positively weighted samples"));
        }
        Ok(dataset)
    }

    pub fn reset_epoch(&mut self, epoch: u64) {
        self.failed = false;
        self.rng = StdRng::seed_from_u64(self.options.seed.wrapping_add(epoch));
        self.seen.clear();
        self.selections = self
            .shards
            .iter()
            .map(|shard| Selection {
                remaining: shard.len(),
                swaps: HashMap::new(),
            })
            .collect();
    }

    pub fn len(&self) -> usize {
        self.shards.iter().map(Shard::len).sum()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    fn weights(&self) -> impl Iterator<Item = f64> + '_ {
        self.selections.iter().enumerate().map(|(index, state)| {
            if state.remaining == 0 {
                0.0
            } else {
                self.options
                    .shard_weights
                    .as_ref()
                    .map_or(state.remaining as f64, |weights| weights[index])
            }
        })
    }

    fn block(&mut self, shard: usize, index: usize) -> Result<Block> {
        let file = if let Some(position) = self.files.iter().position(|(id, _)| *id == shard) {
            self.files.remove(position).unwrap().1
        } else {
            self.shards[shard].open_file()?
        };
        self.files.push_front((shard, file));
        if self.files.len() > self.options.max_open_shards {
            self.files.pop_back();
        }
        self.shards[shard].read_block(index, &mut self.files.front_mut().unwrap().1)
    }

    /// Returns a short final batch and then an empty batch in non-repeating mode.
    pub fn next_batch(&mut self) -> Result<Vec<Sample>> {
        if self.failed {
            return Err(invalid(
                "Sampling failed in this epoch; reset the epoch before retrying",
            ));
        }
        let result = self.read_batch();
        self.failed = result.is_err();
        result
    }

    fn read_batch(&mut self) -> Result<Vec<Sample>> {
        let mut blocks = Vec::with_capacity(self.options.batch_size);
        let weights = self.weights().collect::<Vec<_>>();
        let mut distribution = WeightedIndex::new(weights).ok();
        while blocks.len() < self.options.batch_size {
            let Some(weights) = &distribution else { break };
            let shard = weights.sample(&mut self.rng);
            let selection = &mut self.selections[shard];
            let mut sample = self.rng.gen_range(0..selection.remaining);
            if self.options.without_replacement {
                let before = selection.remaining;
                sample = selection.remove(sample);
                if selection.remaining == 0 {
                    distribution = WeightedIndex::new(self.weights()).ok();
                } else if self.options.shard_weights.is_none() {
                    distribution
                        .as_mut()
                        .unwrap()
                        .update_weights(&[(shard, &((before - 1) as f64))])
                        .map_err(|error| invalid(error.to_string()))?;
                }
                if !self
                    .seen
                    .insert(self.shards[shard].sample_id(sample).unwrap())
                {
                    continue;
                }
            }
            blocks.push(self.block(shard, sample)?);
        }
        if let Some(pool) = &self.workers {
            pool.install(|| blocks.into_par_iter().map(Block::decode).collect())
        } else {
            blocks.into_iter().map(Block::decode).collect()
        }
    }
}
