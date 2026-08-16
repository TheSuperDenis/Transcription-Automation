"use strict";

(function () {
  const DEFAULTS = Object.freeze({
    createJobPath: "/api/jobs",
    configPath: "/api/config",
    healthPath: "/healthz",
    jobPathTemplate: "/api/jobs/{id}",
    downloadPathTemplate: "/api/jobs/{id}/download",
    openFolderPath: "/api/open-output-folder",
    uploadField: "file",
    pollIntervalMs: 1200,
    maxFileBytes: null,
    allowedExtensions: ["mov", "mp4", "m4a", "mp3", "wav", "webm", "mkv", "flac", "ogg"],
    models: [],
    csrfToken: "",
    openFolderEnabled: true
  });

  const TERMINAL_SUCCESS = new Set(["complete", "completed", "done", "success", "succeeded", "finished"]);
  const TERMINAL_FAILURE = new Set(["error", "failed", "failure", "cancelled", "canceled"]);

  const elements = {
    uploadSection: document.getElementById("upload-section"),
    uploadForm: document.getElementById("upload-form"),
    fileInput: document.getElementById("file-input"),
    dropZone: document.getElementById("drop-zone"),
    sizeNote: document.getElementById("size-note"),
    formatSummary: document.getElementById("format-summary"),
    selectedFile: document.getElementById("selected-file"),
    fileExtension: document.getElementById("file-extension"),
    fileName: document.getElementById("file-name"),
    fileMeta: document.getElementById("file-meta"),
    removeFile: document.getElementById("remove-file"),
    modelRow: document.getElementById("model-row"),
    modelSelect: document.getElementById("model-select"),
    transcribeButton: document.getElementById("transcribe-button"),
    processingSection: document.getElementById("processing-section"),
    processingHeading: document.getElementById("processing-heading"),
    processingDetail: document.getElementById("processing-detail"),
    progressTrack: document.getElementById("progress-track"),
    progressBar: document.getElementById("progress-bar"),
    elapsedTime: document.getElementById("elapsed-time"),
    jobFile: document.getElementById("job-file"),
    jobModel: document.getElementById("job-model"),
    jobDevice: document.getElementById("job-device"),
    resultSection: document.getElementById("result-section"),
    resultDuration: document.getElementById("result-duration"),
    resultFileName: document.getElementById("result-file-name"),
    resultStats: document.getElementById("result-stats"),
    transcriptText: document.getElementById("transcript-text"),
    copyButton: document.getElementById("copy-button"),
    copyButtonLabel: document.getElementById("copy-button-label"),
    downloadButton: document.getElementById("download-button"),
    openFolderButton: document.getElementById("open-folder-button"),
    anotherButton: document.getElementById("another-button"),
    errorSection: document.getElementById("error-section"),
    errorMessage: document.getElementById("error-message"),
    dismissError: document.getElementById("dismiss-error"),
    serverIndicator: document.getElementById("server-indicator"),
    serverStatus: document.getElementById("server-status"),
    toast: document.getElementById("toast")
  };

  const state = {
    config: { ...DEFAULTS },
    selectedFile: null,
    jobId: null,
    statusPath: null,
    downloadPath: null,
    startedAt: null,
    elapsedTimer: null,
    pollTimer: null,
    pollGeneration: 0,
    consecutivePollFailures: 0,
    toastTimer: null
  };

  function firstDefined(source, paths, fallback) {
    for (const path of paths) {
      let current = source;
      for (const part of path.split(".")) {
        if (current === null || typeof current !== "object" || !(part in current)) {
          current = undefined;
          break;
        }
        current = current[part];
      }
      if (current !== undefined && current !== null) {
        return current;
      }
    }
    return fallback;
  }

  function asLocalPath(value, fallback) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) {
      return fallback;
    }

    try {
      const placeholder = "__LOCAL_TRANSCRIBER_JOB_ID__";
      const candidate = value.replace(/\{id\}/g, placeholder);
      const parsed = new URL(candidate, window.location.origin);
      if (parsed.origin !== window.location.origin) {
        return fallback;
      }
      return `${parsed.pathname}${parsed.search}`.replace(new RegExp(placeholder, "g"), "{id}");
    } catch (_error) {
      return fallback;
    }
  }

  function positiveNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function normalizeList(value) {
    if (!Array.isArray(value)) {
      return [];
    }
    return value
      .map((item) => {
        if (typeof item === "string") {
          return { value: item, label: item };
        }
        if (item && typeof item === "object") {
          const itemValue = item.value || item.id || item.name || item.model;
          if (typeof itemValue === "string") {
            return { value: itemValue, label: String(item.label || item.display_name || itemValue) };
          }
        }
        return null;
      })
      .filter(Boolean);
  }

  function normalizeExtensions(value) {
    if (!Array.isArray(value)) {
      return DEFAULTS.allowedExtensions.slice();
    }
    const normalized = value
      .map((item) => String(item).trim().toLowerCase().replace(/^\./, ""))
      .filter((item) => /^[a-z0-9]{2,10}$/.test(item));
    return normalized.length ? Array.from(new Set(normalized)) : DEFAULTS.allowedExtensions.slice();
  }

  function readCookie(name) {
    const encoded = `${encodeURIComponent(name)}=`;
    for (const segment of document.cookie.split(";")) {
      const trimmed = segment.trim();
      if (trimmed.startsWith(encoded)) {
        return decodeURIComponent(trimmed.slice(encoded.length));
      }
    }
    return "";
  }

  function csrfFromPage() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return (meta && meta.getAttribute("content")) || readCookie("csrf_token") || readCookie("csrftoken") || "";
  }

  function normalizeConfig(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const endpoints = firstDefined(source, ["endpoints", "api.endpoints"], {});
    const features = firstDefined(source, ["features"], {});
    const models = normalizeList(firstDefined(source, ["models", "available_models", "whisper.models"], []));
    const openFolderSetting = firstDefined(
      source,
      [
        "open_folder_enabled",
        "openOutputFolderSupported",
        "features.open_output_folder",
        "features.openFolder",
        "capabilities.open_output_folder"
      ],
      undefined
    );

    return {
      ...DEFAULTS,
      createJobPath: asLocalPath(
        firstDefined(endpoints, ["create_job", "createJob", "jobs", "transcriptions"], DEFAULTS.createJobPath),
        DEFAULTS.createJobPath
      ),
      healthPath: asLocalPath(
        firstDefined(endpoints, ["health", "healthz"], DEFAULTS.healthPath),
        DEFAULTS.healthPath
      ),
      jobPathTemplate: asLocalPath(
        firstDefined(endpoints, ["job", "job_status", "jobStatus", "transcription"], DEFAULTS.jobPathTemplate),
        DEFAULTS.jobPathTemplate
      ),
      downloadPathTemplate: asLocalPath(
        firstDefined(endpoints, ["download", "job_download", "jobDownload"], DEFAULTS.downloadPathTemplate),
        DEFAULTS.downloadPathTemplate
      ),
      openFolderPath: asLocalPath(
        firstDefined(endpoints, ["open_output_folder", "openFolder"], DEFAULTS.openFolderPath),
        DEFAULTS.openFolderPath
      ),
      uploadField: String(firstDefined(source, ["upload_field", "uploadField"], DEFAULTS.uploadField)),
      pollIntervalMs: Math.max(
        500,
        Math.min(10000, positiveNumber(firstDefined(source, ["poll_interval_ms", "pollIntervalMs"], DEFAULTS.pollIntervalMs), DEFAULTS.pollIntervalMs))
      ),
      maxFileBytes: positiveNumber(
        firstDefined(
          source,
          ["max_file_bytes", "max_upload_bytes", "maxUploadBytes", "limits.max_file_bytes", "limits.max_upload_bytes"],
          null
        ),
        null
      ),
      allowedExtensions: normalizeExtensions(
        firstDefined(
          source,
          ["allowed_extensions", "acceptedExtensions", "supported_extensions", "formats", "limits.allowed_extensions"],
          null
        )
      ),
      models,
      csrfToken: String(
        firstDefined(source, ["csrf_token", "csrfToken", "security.csrf_token", "security.csrfToken"], csrfFromPage()) || ""
      ),
      openFolderEnabled: openFolderSetting === undefined ? DEFAULTS.openFolderEnabled : Boolean(openFolderSetting),
      features
    };
  }

  function endpointForJob(template, jobId) {
    const safeId = encodeURIComponent(String(jobId));
    if (template.includes("{id}")) {
      return template.replace("{id}", safeId);
    }
    return `${template.replace(/\/$/, "")}/${safeId}`;
  }

  function extractJobId(payload) {
    const candidate = firstDefined(payload, ["id", "job_id", "jobId", "job.id", "data.id", "data.job_id"], null);
    if (candidate !== null && ["string", "number"].includes(typeof candidate)) {
      return String(candidate);
    }

    const path = firstDefined(payload, ["status_url", "statusUrl", "urls.status", "links.status"], "");
    if (typeof path === "string") {
      const match = path.match(/\/([^/?#]+)\/?(?:[?#].*)?$/);
      return match ? decodeURIComponent(match[1]) : null;
    }
    return null;
  }

  function normalizeProgress(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    let parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    if (parsed >= 0 && parsed <= 1) {
      parsed *= 100;
    }
    return Math.max(0, Math.min(100, parsed));
  }

  function normalizeError(value) {
    if (typeof value === "string") {
      return value;
    }
    if (value && typeof value === "object") {
      return String(value.message || value.detail || value.error || "The local service reported an error.");
    }
    return "The local service reported an error.";
  }

  function normalizeJob(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rawStatus = String(firstDefined(source, ["status", "state", "job.status", "data.status"], "queued")).toLowerCase();
    const transcript = firstDefined(
      source,
      ["transcript", "text", "result.text", "result.transcript", "output.text", "data.transcript", "data.text"],
      null
    );
    const progressValue = firstDefined(source, ["progress", "percent", "progress_percent", "job.progress", "data.progress"], null);
    const statusPath = asLocalPath(
      firstDefined(source, ["status_url", "statusUrl", "urls.status", "links.status"], ""),
      null
    );
    const downloadPath = asLocalPath(
      firstDefined(source, ["download_url", "downloadUrl", "job.downloadUrl", "urls.download", "links.download"], ""),
      null
    );

    return {
      id: extractJobId(source),
      status: rawStatus,
      stage: String(firstDefined(source, ["stage", "step", "phase", "job.stage", "data.stage"], rawStatus)),
      detail: firstDefined(source, ["detail", "status_message", "statusMessage", "message", "job.detail", "data.detail"], ""),
      progress: normalizeProgress(progressValue),
      transcript: transcript === null || transcript === undefined ? null : String(transcript),
      model: firstDefined(source, ["model", "model_name", "modelName", "job.model", "runtime.model", "metadata.model"], null),
      device: firstDefined(source, ["device", "job.device", "runtime.device", "metadata.device", "compute_device"], null),
      fileName: firstDefined(source, ["original_filename", "filename", "file_name", "job.filename", "metadata.filename"], null),
      wordCount: positiveNumber(firstDefined(source, ["word_count", "wordCount", "result.word_count", "metadata.word_count"], null), null),
      mediaDuration: positiveNumber(firstDefined(source, ["duration_seconds", "media_duration", "metadata.duration_seconds"], null), null),
      processingSeconds: positiveNumber(firstDefined(source, ["processing_seconds", "elapsed_seconds", "metadata.processing_seconds"], null), null),
      error: firstDefined(source, ["error", "failure", "job.error", "data.error"], null),
      statusPath,
      downloadPath
    };
  }

  function humanBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) {
      return "Unknown size";
    }
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let unit = units[0];
    for (let index = 1; index < units.length && value >= 1024; index += 1) {
      value /= 1024;
      unit = units[index];
    }
    const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
    return `${value.toFixed(digits)} ${unit}`;
  }

  function formatClock(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    if (hours > 0) {
      return [hours, minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
    }
    return [minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function extensionOf(fileName) {
    const match = String(fileName).toLowerCase().match(/\.([a-z0-9]{2,10})$/);
    return match ? match[1] : "";
  }

  function titleCase(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function truncate(value, maximum) {
    const stringValue = String(value || "");
    return stringValue.length > maximum ? `${stringValue.slice(0, maximum - 1)}…` : stringValue;
  }

  function setServerState(kind, message) {
    elements.serverIndicator.className = `status-indicator is-${kind}`;
    elements.serverStatus.textContent = message;
  }

  function updateElapsed() {
    if (!state.startedAt) {
      elements.elapsedTime.textContent = "00:00";
      return;
    }
    elements.elapsedTime.textContent = formatClock((Date.now() - state.startedAt) / 1000);
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    state.startedAt = Date.now();
    updateElapsed();
    state.elapsedTimer = window.setInterval(updateElapsed, 1000);
  }

  function stopElapsedTimer() {
    if (state.elapsedTimer !== null) {
      window.clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
  }

  function setProgress(percent) {
    if (percent === null || percent === undefined) {
      elements.progressTrack.classList.add("is-indeterminate");
      elements.progressBar.removeAttribute("value");
      elements.progressBar.removeAttribute("aria-valuenow");
      return;
    }
    const safePercent = Math.max(0, Math.min(100, Number(percent)));
    elements.progressTrack.classList.remove("is-indeterminate");
    elements.progressBar.value = safePercent;
    elements.progressBar.setAttribute("aria-valuenow", String(Math.round(safePercent)));
  }

  function showError(message) {
    elements.errorMessage.textContent = String(message || "Please try again.");
    elements.errorSection.hidden = false;
    elements.errorSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function hideError() {
    elements.errorSection.hidden = true;
    elements.errorMessage.textContent = "";
  }

  function showToast(message) {
    if (state.toastTimer !== null) {
      window.clearTimeout(state.toastTimer);
    }
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      elements.toast.hidden = true;
      elements.toast.textContent = "";
      state.toastTimer = null;
    }, 3200);
  }

  function updateFilePresentation() {
    const file = state.selectedFile;
    const hasFile = Boolean(file);
    elements.dropZone.hidden = hasFile;
    elements.selectedFile.hidden = !hasFile;
    elements.transcribeButton.disabled = !hasFile;
    if (!file) {
      elements.fileInput.value = "";
      elements.fileName.textContent = "";
      elements.fileMeta.textContent = "";
      elements.fileExtension.textContent = "FILE";
      return;
    }

    const extension = extensionOf(file.name);
    elements.fileExtension.textContent = (extension || "FILE").toUpperCase();
    elements.fileName.textContent = file.name;
    elements.fileMeta.textContent = `${humanBytes(file.size)} · Ready to transcribe`;
  }

  function validateFile(file) {
    if (!(file instanceof File)) {
      return "Choose a valid audio or video file.";
    }
    const extension = extensionOf(file.name);
    if (!extension || !state.config.allowedExtensions.includes(extension)) {
      return `That file type is not supported. Choose ${state.config.allowedExtensions.map((item) => `.${item}`).join(", ")}.`;
    }
    if (state.config.maxFileBytes && file.size > state.config.maxFileBytes) {
      return `That file is ${humanBytes(file.size)}. The local limit is ${humanBytes(state.config.maxFileBytes)}.`;
    }
    if (file.size === 0) {
      return "That file is empty. Choose a recording that contains audio.";
    }
    return null;
  }

  function chooseFile(file) {
    hideError();
    const validationError = validateFile(file);
    if (validationError) {
      state.selectedFile = null;
      updateFilePresentation();
      showError(validationError);
      return;
    }
    state.selectedFile = file;
    updateFilePresentation();
  }

  function cancelPolling() {
    state.pollGeneration += 1;
    if (state.pollTimer !== null) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function updateProcessing(job) {
    if (job.model) {
      elements.jobModel.textContent = String(job.model);
    }
    if (job.device) {
      elements.jobDevice.textContent = String(job.device);
    }
    if (job.fileName) {
      elements.jobFile.textContent = String(job.fileName);
    }

    const stage = job.stage || job.status;
    const stageKey = String(stage).toLowerCase();
    const stageNames = {
      queued: "Queued for transcription",
      pending: "Queued for transcription",
      preparing: "Preparing your recording",
      extracting: "Reading the audio track",
      loading: "Loading Whisper",
      running: "Listening and transcribing",
      transcribing: "Listening and transcribing",
      processing: "Listening and transcribing",
      saving: "Saving your transcript"
    };
    const detailDefaults = {
      queued: "Your job is next in the local queue.",
      pending: "Your job is next in the local queue.",
      preparing: "Checking the media and preparing its audio track…",
      extracting: "Extracting audio locally with FFmpeg…",
      loading: "Loading the selected speech model…",
      running: "Whisper is turning the complete recording into text…",
      transcribing: "Whisper is turning the complete recording into text…",
      processing: "Whisper is turning the complete recording into text…",
      saving: "Writing a UTF-8 text file to the output folder…"
    };

    elements.processingHeading.textContent = stageNames[stageKey] || titleCase(stage) || "Transcribing locally";
    elements.processingDetail.textContent = job.detail ? String(job.detail) : (detailDefaults[stageKey] || "The local transcriber is working on your recording…");
    setProgress(job.progress);
  }

  function wordCount(text) {
    const trimmed = String(text || "").trim();
    return trimmed ? trimmed.split(/\s+/).length : 0;
  }

  async function transcriptForJob(job) {
    if (job.transcript !== null) {
      return job.transcript;
    }

    const downloadPath = job.downloadPath || state.downloadPath || endpointForJob(state.config.downloadPathTemplate, state.jobId);
    elements.processingHeading.textContent = "Loading your transcript";
    elements.processingDetail.textContent = "Reading the finished text file from the local service…";
    setProgress(100);

    const response = await fetch(downloadPath, {
      method: "GET",
      headers: requestHeaders(false),
      credentials: "same-origin",
      cache: "no-store"
    });
    const text = await response.text();
    if (!response.ok) {
      let message = text;
      try {
        const payload = text ? JSON.parse(text) : {};
        message = normalizeError(firstDefined(payload, ["error", "message", "detail"], "The transcript file could not be read."));
      } catch (_error) {
        message = text || `The transcript file could not be read (${response.status}).`;
      }
      throw new Error(message);
    }
    return text;
  }

  async function showResult(job) {
    const transcript = await transcriptForJob(job);
    cancelPolling();
    stopElapsedTimer();
    elements.processingSection.hidden = true;
    elements.resultSection.hidden = false;
    hideError();

    const finalWordCount = job.wordCount || wordCount(transcript);
    const processingSeconds = job.processingSeconds || (state.startedAt ? (Date.now() - state.startedAt) / 1000 : null);
    const fileName = job.fileName || (state.selectedFile && state.selectedFile.name) || "Recording";

    elements.transcriptText.textContent = transcript;
    elements.resultFileName.textContent = fileName;
    elements.resultStats.textContent = `${finalWordCount.toLocaleString()} ${finalWordCount === 1 ? "word" : "words"} · Plain text`;
    elements.resultDuration.textContent = processingSeconds ? `Completed in ${formatClock(processingSeconds)}` : "Complete";

    state.downloadPath = job.downloadPath || endpointForJob(state.config.downloadPathTemplate, state.jobId);
    elements.downloadButton.href = state.downloadPath;
    elements.openFolderButton.hidden = !state.config.openFolderEnabled;
    elements.copyButtonLabel.textContent = "Copy transcript";
    elements.resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function failJob(message) {
    cancelPolling();
    stopElapsedTimer();
    elements.processingSection.hidden = true;
    elements.uploadSection.hidden = false;
    elements.transcribeButton.disabled = !state.selectedFile;
    showError(message);
  }

  function requestHeaders(includeJson) {
    const headers = {};
    if (includeJson) {
      headers.Accept = "application/json";
    }
    if (state.config.csrfToken) {
      headers["X-CSRF-Token"] = state.config.csrfToken;
      headers["X-CSRFToken"] = state.config.csrfToken;
    }
    return headers;
  }

  async function parseResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    const text = await response.text();
    if (!text) {
      return {};
    }
    try {
      return JSON.parse(text);
    } catch (_error) {
      return { message: text };
    }
  }

  async function pollJob(generation) {
    if (generation !== state.pollGeneration || !state.jobId) {
      return;
    }

    try {
      const response = await fetch(state.statusPath, {
        method: "GET",
        headers: requestHeaders(true),
        credentials: "same-origin",
        cache: "no-store"
      });
      const payload = await parseResponse(response);
      if (!response.ok) {
        throw new Error(normalizeError(firstDefined(payload, ["error", "message", "detail"], `Status check failed (${response.status}).`)));
      }

      state.consecutivePollFailures = 0;
      const job = normalizeJob(payload);
      if (job.statusPath) {
        state.statusPath = job.statusPath;
      }
      updateProcessing(job);

      if (TERMINAL_SUCCESS.has(job.status)) {
        await showResult(job);
        return;
      }
      if (TERMINAL_FAILURE.has(job.status)) {
        failJob(normalizeError(job.error || job.detail));
        return;
      }
    } catch (error) {
      state.consecutivePollFailures += 1;
      if (state.consecutivePollFailures >= 4) {
        failJob(error instanceof Error ? error.message : "Lost contact with the local transcriber.");
        return;
      }
      elements.processingDetail.textContent = "The local service is briefly unavailable. Reconnecting…";
    }

    if (generation === state.pollGeneration) {
      state.pollTimer = window.setTimeout(() => pollJob(generation), state.config.pollIntervalMs);
    }
  }

  function uploadJob(formData) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", state.config.createJobPath, true);
      xhr.responseType = "text";
      xhr.withCredentials = true;
      xhr.setRequestHeader("Accept", "application/json");
      if (state.config.csrfToken) {
        xhr.setRequestHeader("X-CSRF-Token", state.config.csrfToken);
        xhr.setRequestHeader("X-CSRFToken", state.config.csrfToken);
      }

      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) {
          setProgress(null);
          return;
        }
        const percent = (event.loaded / event.total) * 100;
        setProgress(percent);
        elements.processingDetail.textContent = `Uploading to the local service… ${Math.round(percent)}%`;
      });

      xhr.addEventListener("load", () => {
        let payload = {};
        if (xhr.responseText) {
          try {
            payload = JSON.parse(xhr.responseText);
          } catch (_error) {
            payload = { message: xhr.responseText };
          }
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload);
          return;
        }
        reject(new Error(normalizeError(firstDefined(payload, ["error", "message", "detail"], `Upload failed (${xhr.status}).`))));
      });

      xhr.addEventListener("error", () => reject(new Error("Could not reach the local transcription service.")));
      xhr.addEventListener("abort", () => reject(new Error("The upload was cancelled.")));
      xhr.send(formData);
    });
  }

  async function startTranscription(event) {
    event.preventDefault();
    if (!state.selectedFile) {
      showError("Choose an audio or video recording first.");
      return;
    }

    const validationError = validateFile(state.selectedFile);
    if (validationError) {
      showError(validationError);
      return;
    }

    cancelPolling();
    hideError();
    elements.resultSection.hidden = true;
    elements.uploadSection.hidden = true;
    elements.processingSection.hidden = false;
    elements.processingHeading.textContent = "Uploading your recording";
    elements.processingDetail.textContent = "Uploading the file to the local transcriber…";
    elements.jobFile.textContent = truncate(state.selectedFile.name, 48);
    elements.jobModel.textContent = elements.modelSelect.value !== "auto" ? elements.modelSelect.value : "Auto";
    elements.jobDevice.textContent = "Selecting…";
    setProgress(0);
    startElapsedTimer();
    elements.processingSection.scrollIntoView({ behavior: "smooth", block: "start" });

    const formData = new FormData();
    formData.append(state.config.uploadField || "file", state.selectedFile, state.selectedFile.name);
    if (!elements.modelRow.hidden && elements.modelSelect.value) {
      formData.append("model", elements.modelSelect.value);
    }

    try {
      const payload = await uploadJob(formData);
      const job = normalizeJob(payload);
      state.jobId = job.id || extractJobId(payload);
      if (!state.jobId) {
        throw new Error("The local service accepted the file but did not return a job ID.");
      }

      state.statusPath = job.statusPath || endpointForJob(state.config.jobPathTemplate, state.jobId);
      state.downloadPath = job.downloadPath || endpointForJob(state.config.downloadPathTemplate, state.jobId);
      updateProcessing(job);

      if (TERMINAL_SUCCESS.has(job.status)) {
        await showResult(job);
        return;
      }
      if (TERMINAL_FAILURE.has(job.status)) {
        throw new Error(normalizeError(job.error || job.detail));
      }

      state.consecutivePollFailures = 0;
      const generation = state.pollGeneration;
      await pollJob(generation);
    } catch (error) {
      failJob(error instanceof Error ? error.message : "The transcription could not be started.");
    }
  }

  async function copyTranscript() {
    const text = elements.transcriptText.textContent || "";
    if (!text) {
      showToast("There is no transcript text to copy.");
      return;
    }

    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.className = "clipboard-fallback";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand("copy");
        textarea.remove();
        if (!copied) {
          throw new Error("Copy was not available.");
        }
      }
      elements.copyButtonLabel.textContent = "Copied";
      showToast("Transcript copied to the clipboard.");
      window.setTimeout(() => {
        elements.copyButtonLabel.textContent = "Copy transcript";
      }, 1800);
    } catch (_error) {
      showToast("Could not copy automatically. Select the transcript text and copy it manually.");
    }
  }

  async function openOutputFolder() {
    elements.openFolderButton.disabled = true;
    try {
      const response = await fetch(state.config.openFolderPath, {
        method: "POST",
        headers: requestHeaders(true),
        credentials: "same-origin"
      });
      const payload = await parseResponse(response);
      if (response.status === 404 || response.status === 405 || response.status === 501) {
        state.config.openFolderEnabled = false;
        elements.openFolderButton.hidden = true;
        showToast("Open folder is not available in this build.");
        return;
      }
      if (!response.ok) {
        throw new Error(normalizeError(firstDefined(payload, ["error", "message", "detail"], "Could not open the output folder.")));
      }
      showToast(String(firstDefined(payload, ["message"], "Output folder opened.")));
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Could not open the output folder.");
    } finally {
      elements.openFolderButton.disabled = false;
    }
  }

  function resetForAnother() {
    cancelPolling();
    stopElapsedTimer();
    hideError();
    state.selectedFile = null;
    state.jobId = null;
    state.statusPath = null;
    state.downloadPath = null;
    state.startedAt = null;
    elements.processingSection.hidden = true;
    elements.resultSection.hidden = true;
    elements.uploadSection.hidden = false;
    elements.transcriptText.textContent = "";
    elements.downloadButton.href = "#";
    updateFilePresentation();
    elements.dropZone.focus();
    window.scrollTo({ top: elements.uploadSection.offsetTop - 24, behavior: "smooth" });
  }

  function populateConfigUi() {
    const extensions = state.config.allowedExtensions;
    elements.fileInput.accept = extensions.map((extension) => `.${extension}`).join(",");
    const visibleFormats = extensions.slice(0, 5).map((extension) => extension.toUpperCase());
    elements.formatSummary.textContent = `${visibleFormats.join(" · ")}${extensions.length > 5 ? " + more" : ""}`;
    elements.sizeNote.textContent = state.config.maxFileBytes
      ? `One file at a time · Up to ${humanBytes(state.config.maxFileBytes)}`
      : "One audio or video file at a time";

    while (elements.modelSelect.options.length > 1) {
      elements.modelSelect.remove(1);
    }
    for (const model of state.config.models) {
      if (model.value.toLowerCase() === "auto") {
        continue;
      }
      const option = document.createElement("option");
      option.value = model.value;
      option.textContent = model.label;
      elements.modelSelect.appendChild(option);
    }
    elements.modelRow.hidden = state.config.models.length === 0;
    elements.openFolderButton.hidden = !state.config.openFolderEnabled;
  }

  async function loadConfiguration() {
    let configPayload = {};
    try {
      const response = await fetch(DEFAULTS.configPath, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store"
      });
      if (response.ok) {
        configPayload = await parseResponse(response);
      }
    } catch (_error) {
      configPayload = {};
    }

    state.config = normalizeConfig(configPayload);
    populateConfigUi();

    try {
      const healthResponse = await fetch(state.config.healthPath, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store"
      });
      if (!healthResponse.ok) {
        throw new Error("Health check failed.");
      }
      const health = await parseResponse(healthResponse);
      const healthStatus = String(firstDefined(health, ["status", "state"], "ready")).toLowerCase();
      if (["error", "failed", "unhealthy"].includes(healthStatus)) {
        throw new Error("The service is not ready.");
      }
      setServerState("ready", "Ready on this computer");
    } catch (_error) {
      setServerState("error", "Local service needs attention");
    }
  }

  function bindEvents() {
    elements.dropZone.addEventListener("click", () => elements.fileInput.click());
    elements.dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        elements.fileInput.click();
      }
    });

    elements.fileInput.addEventListener("change", () => {
      const files = elements.fileInput.files;
      if (files && files.length) {
        chooseFile(files[0]);
      }
    });

    for (const eventName of ["dragenter", "dragover"]) {
      elements.dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = "copy";
        }
        elements.dropZone.classList.add("is-dragging");
      });
    }

    for (const eventName of ["dragleave", "dragend"]) {
      elements.dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        elements.dropZone.classList.remove("is-dragging");
      });
    }

    elements.dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      elements.dropZone.classList.remove("is-dragging");
      const files = event.dataTransfer && event.dataTransfer.files;
      if (!files || files.length === 0) {
        showError("No file was found in that drop.");
        return;
      }
      if (files.length > 1) {
        showError("Choose one recording at a time.");
        return;
      }
      chooseFile(files[0]);
    });

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
    elements.removeFile.addEventListener("click", () => {
      state.selectedFile = null;
      updateFilePresentation();
      elements.dropZone.focus();
    });
    elements.uploadForm.addEventListener("submit", startTranscription);
    elements.copyButton.addEventListener("click", copyTranscript);
    elements.openFolderButton.addEventListener("click", openOutputFolder);
    elements.anotherButton.addEventListener("click", resetForAnother);
    elements.dismissError.addEventListener("click", hideError);
  }

  bindEvents();
  updateFilePresentation();
  loadConfiguration();
})();
