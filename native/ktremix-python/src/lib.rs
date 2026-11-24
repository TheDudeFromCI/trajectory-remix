use ktremix::corpus::{CorpusOptions, Document, Documents, Input};
use ktremix::dataset::{Dataset, SamplingOptions};
use ktremix::shard::{CompileWriter, Sample};
use ktremix::{Compiler, Error, Options, TokenizerProfile, CONTRACT, VERSION};
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::{
    ffi,
    types::{PyIterator, PyModule},
};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

pyo3::create_exception!(_native, Cancelled, PyRuntimeError);

fn error(exc: Error) -> PyErr {
    match exc {
        Error::Cancelled => Cancelled::new_err(exc.to_string()),
        Error::Io(ref io) => {
            if io.kind() == std::io::ErrorKind::AlreadyExists {
                pyo3::exceptions::PyFileExistsError::new_err(io.to_string())
            } else {
                PyOSError::new_err(io.to_string())
            }
        }
        _ => PyValueError::new_err(exc.to_string()),
    }
}

fn parse<T: serde::de::DeserializeOwned>(input: &str) -> PyResult<T> {
    serde_json::from_str(input).map_err(|exc| PyValueError::new_err(exc.to_string()))
}

fn output<T: serde::Serialize>(value: T) -> PyResult<String> {
    serde_json::to_string(&value).map_err(|exc| PyValueError::new_err(exc.to_string()))
}

struct Poll {
    function: Option<Py<PyAny>>,
    exception: Mutex<Option<PyErr>>,
    position: AtomicUsize,
    cancelled: AtomicBool,
}

impl Poll {
    fn new(function: Option<Py<PyAny>>) -> Self {
        Self {
            function,
            exception: Mutex::new(None),
            position: AtomicUsize::new(0),
            cancelled: AtomicBool::new(false),
        }
    }

    fn check(&self) -> bool {
        let Some(function) = &self.function else {
            return false;
        };
        if self.cancelled.load(Ordering::Acquire) {
            return true;
        }
        // Keep native scheduling outside the GIL. A Python callback is only
        // needed periodically and at the Python caller's phase boundaries.
        if !self
            .position
            .fetch_add(1, Ordering::Relaxed)
            .is_multiple_of(64)
        {
            return false;
        }
        Python::attach(|py| {
            match function
                .call0(py)
                .and_then(|value| value.bind(py).is_truthy())
            {
                Ok(result) => {
                    if result {
                        self.cancelled.store(true, Ordering::Release);
                    }
                    result
                }
                Err(exc) => {
                    *self.exception.lock().unwrap() = Some(exc);
                    self.cancelled.store(true, Ordering::Release);
                    true
                }
            }
        })
    }

    fn finish<T>(self, result: ktremix::Result<T>) -> PyResult<T> {
        match self.exception.into_inner().unwrap() {
            Some(exc) => Err(exc),
            None => result.map_err(error),
        }
    }
}

#[pyclass(name = "Compiler", module = "ktremix_native._native")]
struct CompilerBinding(Compiler);

#[pymethods]
impl CompilerBinding {
    #[new]
    #[pyo3(signature = (profile, options, settings=None))]
    fn new(py: Python<'_>, profile: &str, options: &str, settings: Option<&str>) -> PyResult<Self> {
        let profile: TokenizerProfile = parse(profile)?;
        let options: Options = parse(options)?;
        py.detach(|| Compiler::new(profile, settings, options))
            .map(Self)
            .map_err(error)
    }

    fn tokenizer_json(&self) -> PyResult<String> {
        self.0.tokenizer_json().map_err(error)
    }

    fn manifest_json(&self) -> PyResult<String> {
        output(self.0.manifest())
    }

    #[pyo3(signature = (source, cancelled=None))]
    fn compile_json(
        &self,
        py: Python<'_>,
        source: &str,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<String> {
        let poll = Poll::new(cancelled);
        let result = py.detach(|| {
            self.0
                .compile_cancellable(source, || poll.check())
                .and_then(|compiled| serde_json::to_string(&compiled).map_err(Error::from))
        });
        poll.finish(result)
    }

    fn report_json(&self, py: Python<'_>, source: &str) -> PyResult<String> {
        output(py.detach(|| self.0.report(source)))
    }

    fn reports_json(
        &self,
        py: Python<'_>,
        sources: Vec<String>,
        workers: usize,
    ) -> PyResult<String> {
        output(
            py.detach(|| self.0.reports(&sources, workers))
                .map_err(error)?,
        )
    }

    fn literal_ids(&self, py: Python<'_>, text: &str) -> PyResult<Vec<u32>> {
        py.detach(|| self.0.literal_ids(text)).map_err(error)
    }

    #[pyo3(signature = (source, max_frames, cancelled=None))]
    fn preview_json(
        &self,
        py: Python<'_>,
        source: &str,
        max_frames: usize,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<String> {
        let poll = Poll::new(cancelled);
        let result = py.detach(|| {
            self.0
                .preview(source, max_frames, || poll.check())
                .and_then(|preview| serde_json::to_string(&preview).map_err(Error::from))
        });
        poll.finish(result)
    }

    #[pyo3(signature = (paths, directory, input, options, provenance, max_document_bytes, cancelled=None))]
    #[allow(clippy::too_many_arguments)]
    fn convert_corpus(
        &self,
        py: Python<'_>,
        paths: Vec<PathBuf>,
        directory: PathBuf,
        input: &str,
        options: &str,
        provenance: &str,
        max_document_bytes: usize,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<Vec<PathBuf>> {
        let format = match input {
            "text" => Input::Text,
            "jsonl" => Input::JsonLines,
            _ => return Err(PyValueError::new_err("input must be text or jsonl")),
        };
        if max_document_bytes == 0 {
            return Err(PyValueError::new_err("max_document_bytes must be positive"));
        }
        let options: CorpusOptions = parse(options)?;
        let provenance = parse(provenance)?;
        let poll = Poll::new(cancelled);
        let result = py.detach(|| {
            self.0.convert_corpus(
                Documents::new(paths.into_iter(), format, max_document_bytes),
                directory,
                &options,
                provenance,
                || poll.check(),
            )
        });
        poll.finish(result)
    }

    #[pyo3(signature = (documents, directory, options, provenance, max_document_bytes, cancelled=None))]
    #[allow(clippy::too_many_arguments)]
    fn convert_documents(
        &self,
        py: Python<'_>,
        documents: Py<PyIterator>,
        directory: PathBuf,
        options: &str,
        provenance: &str,
        max_document_bytes: usize,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<Vec<PathBuf>> {
        if max_document_bytes == 0 {
            return Err(PyValueError::new_err("max_document_bytes must be positive"));
        }
        let options: CorpusOptions = parse(options)?;
        let provenance = parse(provenance)?;
        let poll = Poll::new(cancelled);
        let mut exception = None;
        let result = py.detach(|| {
            let documents = std::iter::from_fn(|| {
                let next = Python::attach(|py| {
                    documents
                        .bind(py)
                        .clone()
                        .next()
                        .map(|item| item.and_then(|value| value.extract::<String>()))
                });
                next.map(|item| {
                    let text = item.map_err(|error| {
                        exception = Some(error);
                        Error::Invalid("Corpus document iterator failed".into())
                    })?;
                    if text.len() > max_document_bytes {
                        return Err(Error::Invalid("Corpus document exceeds byte limit".into()));
                    }
                    serde_json::from_str::<Document>(&text).map_err(Error::from)
                })
            });
            self.0
                .convert_corpus(documents, directory, &options, provenance, || poll.check())
        });
        if let Some(exception) = exception {
            return Err(exception);
        }
        poll.finish(result)
    }
}

#[pyfunction]
#[pyo3(signature = (source, settings=None))]
fn report_json(py: Python<'_>, source: &str, settings: Option<&str>) -> PyResult<String> {
    py.detach(|| {
        Compiler::structural_report(source, settings)
            .and_then(|report| serde_json::to_string(&report).map_err(Error::from))
    })
    .map_err(error)
}

#[pyfunction]
#[pyo3(signature = (sources, workers, settings=None))]
fn reports_json(
    py: Python<'_>,
    sources: Vec<String>,
    workers: usize,
    settings: Option<&str>,
) -> PyResult<String> {
    output(
        py.detach(|| Compiler::structural_reports(&sources, settings, workers))
            .map_err(error)?,
    )
}

#[pyclass(name = "ShardWriter", module = "ktremix_native._native")]
struct WriterBinding(Option<CompileWriter>);

#[pymethods]
impl WriterBinding {
    #[new]
    fn new(
        py: Python<'_>,
        path: PathBuf,
        manifest: &str,
        count: usize,
        codec: &str,
        workers: usize,
    ) -> PyResult<Self> {
        let manifest = parse(manifest)?;
        let codec = codec.parse().map_err(error)?;
        py.detach(|| CompileWriter::new(path, manifest, count, codec, workers))
            .map(|writer| Self(Some(writer)))
            .map_err(error)
    }

    #[pyo3(signature = (compiler, sources, cancelled=None))]
    fn append(
        &mut self,
        py: Python<'_>,
        compiler: &CompilerBinding,
        sources: Vec<String>,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<()> {
        let poll = Poll::new(cancelled);
        let mut writer = self.take()?;
        let result = py.detach(|| writer.append(&compiler.0, sources, || poll.check()));
        poll.finish(result)?;
        self.0 = Some(writer);
        Ok(())
    }

    #[pyo3(signature = (compiler, paths, cancelled=None))]
    fn append_files(
        &mut self,
        py: Python<'_>,
        compiler: &CompilerBinding,
        paths: Vec<PathBuf>,
        cancelled: Option<Py<PyAny>>,
    ) -> PyResult<()> {
        let poll = Poll::new(cancelled);
        let mut writer = self.take()?;
        let result = py.detach(|| writer.append_files(&compiler.0, paths, || poll.check()));
        poll.finish(result)?;
        self.0 = Some(writer);
        Ok(())
    }

    fn finish(&mut self, py: Python<'_>) -> PyResult<()> {
        let writer = self.take()?;
        py.detach(|| writer.finish()).map_err(error)
    }
}

impl WriterBinding {
    fn take(&mut self) -> PyResult<CompileWriter> {
        self.0
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("Shard writer is closed"))
    }
}

#[pyclass(name = "Dataset", module = "ktremix_native._native")]
struct DatasetBinding(Dataset);

#[pymethods]
impl DatasetBinding {
    #[new]
    fn new(py: Python<'_>, paths: Vec<PathBuf>, options: &str) -> PyResult<Self> {
        let options: SamplingOptions = parse(options)?;
        py.detach(|| Dataset::open(paths, options))
            .map(Self)
            .map_err(error)
    }

    fn __len__(&self) -> usize {
        self.0.len()
    }

    fn reset_epoch(&mut self, epoch: u64) {
        self.0.reset_epoch(epoch);
    }

    fn next_batch(&mut self, py: Python<'_>) -> PyResult<Vec<SampleBinding>> {
        py.detach(|| {
            self.0
                .next_batch()
                .map(|batch| batch.into_iter().map(SampleBinding).collect())
        })
        .map_err(error)
    }
}

#[pyclass(name = "Sample", module = "ktremix_native._native", frozen)]
struct SampleBinding(Sample);

#[pymethods]
impl SampleBinding {
    #[getter]
    fn id(&self) -> String {
        self.0.id.to_string()
    }
    #[getter]
    fn rows(&self) -> u32 {
        self.0.rows
    }
    #[getter]
    fn frames(&self) -> u32 {
        self.0.frames
    }
    #[getter]
    fn windows(&self) -> u32 {
        self.0.windows
    }
    #[getter]
    fn targets(&self) -> u32 {
        self.0.targets
    }

    fn metadata_json(&self) -> PyResult<String> {
        output(self.0.metadata().map_err(error)?)
    }

    fn columns(&self) -> Vec<(&'static str, u8, usize, usize)> {
        self.0
            .columns()
            .map(|(name, dtype, range)| (name, dtype, range.start, range.len()))
            .collect()
    }

    unsafe fn __getbuffer__(
        slf: Bound<'_, Self>,
        view: *mut ffi::Py_buffer,
        flags: std::ffi::c_int,
    ) -> PyResult<()> {
        let data = slf.get().0.record_bytes();
        let length = isize::try_from(data.len())
            .map_err(|_| pyo3::exceptions::PyOverflowError::new_err("Record is too large"))?;
        // The sample is frozen, the allocation is never moved or resized,
        // and FillInfo keeps a reference to its owner until the view is released.
        let result = unsafe {
            ffi::PyBuffer_FillInfo(
                view,
                slf.as_ptr(),
                data.as_ptr().cast_mut().cast(),
                length,
                1,
                flags,
            )
        };
        if result == -1 {
            Err(PyErr::fetch(slf.py()))
        } else {
            Ok(())
        }
    }
}

#[pymodule]
fn _native(py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("VERSION", VERSION)?;
    module.add("CONTRACT", CONTRACT)?;
    module.add("Cancelled", py.get_type::<Cancelled>())?;
    module.add_class::<CompilerBinding>()?;
    module.add_class::<WriterBinding>()?;
    module.add_class::<DatasetBinding>()?;
    module.add_class::<SampleBinding>()?;
    module.add_function(wrap_pyfunction!(report_json, module)?)?;
    module.add_function(wrap_pyfunction!(reports_json, module)?)?;
    Ok(())
}
